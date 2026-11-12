"""notification 队列：邮件、短信、微信订阅消息投递并回写 `notification.sentChannels`。"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Final

from sqlalchemy import select

from app.common.exceptions import HttpError
from app.common.responses import iso_timestamp
from app.core.users.models import UserModel
from app.infrastructure.queue import (
    APP_CONTEXT,
    DEFAULT_RETRY,
    NOTIFICATION_QUEUE,
    UnrecoverableError,
    run_job,
    stream_name,
    tasks,
)
from app.integrations.mail import OutgoingMail, SmtpTransport
from app.integrations.sms import AliyunSmsProvider
from app.integrations.wechat import WechatApiError, WechatService, WechatSubscribeMessage
from app.modules.notifications.wechat_subscribe import build_subscribe_message
lazy from app.common.context import AppContext
lazy from app.infrastructure.queue import TaskQueue
lazy from app.modules.notifications.models import NotificationModel
lazy from app.modules.notifications.repository import NotificationRepository

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.orm import InstrumentedAttribute

logger = logging.getLogger("app.queue.notification")

SEND_EMAIL_TASK: Final = "notification:send-email"
SEND_SMS_TASK: Final = "notification:send-sms"
SEND_WECHAT_SUBSCRIBE_TASK: Final = "notification:send-wechat-subscribe"
EMAIL_CHANNEL: Final = "EMAIL"
SMS_CHANNEL: Final = "SMS"
PUSH_CHANNEL: Final = "PUSH"
SUBSCRIBE_UNRECOVERABLE_ERROR_CODES: Final = frozenset({43101, 40003, 40037, 41030, 47003})
"""43101 用户未订阅或次数已用完，40003 openid 无效，40037 模板 ID 无效，
41030 page 无效，47003 模板参数不合法。"""
CONCURRENCY: Final = 5
RATE_LIMIT_MAX: Final = 10
RATE_LIMIT_DURATION_MS: Final = 1000


class JobLimiter:
    """BullMQ `concurrency` 与 `limiter {max, duration}` 的进程内等效：信号量 + 滑动窗口计数。"""

    def __init__(self, concurrency: int, max_jobs: int, duration_ms: int) -> None:
        self.concurrency = concurrency
        self.max_jobs = max_jobs
        self.duration = duration_ms / 1000
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore = asyncio.Semaphore(concurrency)
        self._rate_lock = asyncio.Lock()
        self._started: deque[float] = deque()

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._semaphore = asyncio.Semaphore(self.concurrency)
            self._rate_lock = asyncio.Lock()
            self._started.clear()

    async def _wait_rate(self) -> None:
        async with self._rate_lock:
            while True:
                now = time.monotonic()
                while self._started and now - self._started[0] >= self.duration:
                    self._started.popleft()
                if len(self._started) < self.max_jobs:
                    self._started.append(now)
                    return
                await asyncio.sleep(self.duration - (now - self._started[0]))

    async def run[T](self, job: Callable[[], Awaitable[T]]) -> T:
        self._bind_loop()
        async with self._semaphore:
            await self._wait_rate()
            return await job()


limiter: Final = JobLimiter(CONCURRENCY, RATE_LIMIT_MAX, RATE_LIMIT_DURATION_MS)


async def record_channel_result(
    context: AppContext, notification_id: int, channel: str, sent: bool, error: str | None = None
) -> None:
    """记录渠道发送结果，记录失败只写日志不抛出。"""
    result: dict[str, Any] = {"sent": sent}
    if error:
        result["error"] = error
    result["sentAt"] = iso_timestamp(datetime.now(UTC))
    try:
        async with context.database.transaction() as session:
            await NotificationRepository(session).record_channel_result(
                notification_id, channel, result
            )
    except Exception as record_error:
        logger.warning(
            "Failed to record %s result for notification %s: %s",
            channel,
            notification_id,
            record_error,
        )


async def _user_field(
    context: AppContext, user_id: int, column: InstrumentedAttribute[str | None]
) -> str | None:
    async with context.database.transaction() as session:
        return await session.scalar(
            select(column).where(UserModel.id == user_id, UserModel.deleted_at.is_(None))
        )


def _error_message(error: Exception) -> str:
    """nestjs `error.message`：无 message 的对象响应体 422 为 `Unprocessable Entity Exception`。"""
    if isinstance(error, HttpError) and isinstance(error.response, Mapping):
        message = error.response.get("message")
        if isinstance(message, str):
            return message
        if error.status == 422:
            return "Unprocessable Entity Exception"
    return str(error)


def _is_unrecoverable_subscribe_error(error: Exception) -> bool:
    """重试无法成功的订阅消息错误：不可重试的错误码，或小程序 AppID / Secret 未配置。"""
    if isinstance(error, WechatApiError):
        return error.errcode in SUBSCRIBE_UNRECOVERABLE_ERROR_CODES
    if isinstance(error, HttpError) and isinstance(error.response, Mapping):
        errors = error.response.get("errors")
        return isinstance(errors, Mapping) and errors.get("wechat") == "notConfigured"
    return False


@tasks.task(task_name=SEND_EMAIL_TASK, queue_name=stream_name(NOTIFICATION_QUEUE))
async def send_email(job: dict[str, Any], context: Annotated[AppContext, APP_CONTEXT]) -> None:
    notification_id = int(job["notificationId"])
    user_id = int(job["userId"])

    async def deliver() -> None:
        try:
            email = await _user_field(context, user_id, UserModel.email)
            if not email:
                raise RuntimeError(f"User {user_id} not found or has no email")
            await SmtpTransport(context.settings.mail).send(
                OutgoingMail(
                    to=email,
                    subject=job["title"],
                    text=job["content"],
                    html=f"<p>{job['content']}</p>",
                )
            )
        except Exception as error:
            await record_channel_result(context, notification_id, EMAIL_CHANNEL, False, str(error))
            raise
        await record_channel_result(context, notification_id, EMAIL_CHANNEL, True)
        logger.info("Email sent for notification %s", notification_id)

    await run_job(NOTIFICATION_QUEUE, DEFAULT_RETRY, lambda: limiter.run(deliver))


@tasks.task(task_name=SEND_SMS_TASK, queue_name=stream_name(NOTIFICATION_QUEUE))
async def send_sms(job: dict[str, Any], context: Annotated[AppContext, APP_CONTEXT]) -> None:
    notification_id = int(job["notificationId"])
    user_id = int(job["userId"])

    async def deliver() -> None:
        try:
            phone = await _user_field(context, user_id, UserModel.phone)
            if not phone:
                raise RuntimeError(f"User {user_id} not found or has no phone")
            provider = AliyunSmsProvider(context.settings.sms, context.http_transport)
            result = await provider.send_sms(phone, {"content": job["content"]})
            if not result.success:
                raise RuntimeError(result.message or "SMS send failed")
        except Exception as error:
            await record_channel_result(
                context, notification_id, SMS_CHANNEL, False, _error_message(error)
            )
            raise
        await record_channel_result(context, notification_id, SMS_CHANNEL, True)
        logger.info("SMS sent for notification %s", notification_id)

    await run_job(NOTIFICATION_QUEUE, DEFAULT_RETRY, lambda: limiter.run(deliver))


@tasks.task(task_name=SEND_WECHAT_SUBSCRIBE_TASK, queue_name=stream_name(NOTIFICATION_QUEUE))
async def send_wechat_subscribe(
    job: dict[str, Any], context: Annotated[AppContext, APP_CONTEXT]
) -> None:
    notification_id = int(job["notificationId"])
    user_id = int(job["userId"])
    payload: dict[str, Any] = job["payload"]
    scene = str(payload.get("scene"))

    async def deliver() -> None:
        templates = context.settings.wechat.subscribe_templates
        message = build_subscribe_message(payload, templates.get(scene))
        if message is None:
            logger.debug("Subscribe template for scene %s not configured, skipped", scene)
            return
        open_id = await _user_field(context, user_id, UserModel.wechat_open_id)
        if not open_id:
            await record_channel_result(
                context, notification_id, PUSH_CHANNEL, False, "User has no WeChat openId"
            )
            return
        wechat = WechatService(context.settings.wechat, context.cache, context.http_transport)
        try:
            await wechat.send_subscribe_message(
                WechatSubscribeMessage(
                    open_id=open_id,
                    template_id=message.template_id,
                    page=message.page,
                    data=message.data,
                )
            )
        except Exception as error:
            error_message = _error_message(error)
            await record_channel_result(
                context, notification_id, PUSH_CHANNEL, False, error_message
            )
            if _is_unrecoverable_subscribe_error(error):
                raise UnrecoverableError(error_message) from error
            raise
        await record_channel_result(context, notification_id, PUSH_CHANNEL, True)
        logger.info("WeChat subscribe message sent for notification %s", notification_id)

    await run_job(NOTIFICATION_QUEUE, DEFAULT_RETRY, lambda: limiter.run(deliver))


async def queue_external_notifications(queue: TaskQueue, notification: NotificationModel) -> None:
    """按通知的渠道入队：EMAIL、SMS，以及 metadata 带 wechatSubscribe 的 PUSH。"""
    wechat_subscribe = (notification.metadata_ or {}).get("wechatSubscribe")
    for channel in notification.channels:
        if channel == EMAIL_CHANNEL:
            await queue.enqueue(
                send_email,
                {
                    "notificationId": notification.id,
                    "userId": notification.user_id,
                    "title": notification.title,
                    "content": notification.content,
                },
            )
        elif channel == SMS_CHANNEL:
            await queue.enqueue(
                send_sms,
                {
                    "notificationId": notification.id,
                    "userId": notification.user_id,
                    "content": notification.content,
                },
            )
        elif channel == PUSH_CHANNEL and wechat_subscribe:
            await queue.enqueue(
                send_wechat_subscribe,
                {
                    "notificationId": notification.id,
                    "userId": notification.user_id,
                    "payload": wechat_subscribe,
                },
            )
