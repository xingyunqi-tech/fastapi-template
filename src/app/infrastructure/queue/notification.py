"""notification 队列：站外渠道投递并回写 `notification.sentChannels`；只支持 EMAIL 渠道。"""

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Final

from sqlalchemy import select, text

from app.core.users.models import UserModel
from app.infrastructure.queue import (
    APP_CONTEXT,
    DEFAULT_RETRY,
    NOTIFICATION_QUEUE,
    run_job,
    stream_name,
    tasks,
)
from app.integrations.mail import OutgoingMail, SmtpTransport
lazy from app.common.context import AppContext
lazy from app.infrastructure.queue import TaskQueue
lazy from app.modules.notifications.models import NotificationModel

logger = logging.getLogger("app.queue.notification")

SEND_EMAIL_TASK: Final = "notification:send-email"
EMAIL_CHANNEL: Final = "EMAIL"

_RECORD_CHANNEL_RESULT = text(
    'UPDATE "notification" SET "sentChannels" = "sentChannels" || CAST(:patch AS jsonb), '
    '"updatedAt" = CURRENT_TIMESTAMP WHERE "id" = :id AND "deletedAt" IS NULL'
)


def _js_iso(moment: datetime) -> str:
    return (
        moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
    )


async def record_channel_result(
    context: AppContext, notification_id: int, channel: str, sent: bool, error: str | None = None
) -> None:
    """记录渠道发送结果，记录失败只写日志不抛出。"""
    result: dict[str, Any] = {"sent": sent}
    if error:
        result["error"] = error
    result["sentAt"] = _js_iso(datetime.now(UTC))
    try:
        async with context.database.transaction() as session:
            await session.execute(
                _RECORD_CHANNEL_RESULT,
                {"id": notification_id, "patch": json.dumps({channel: result})},
            )
    except Exception as record_error:
        logger.warning(
            "Failed to record %s result for notification %s: %s",
            channel,
            notification_id,
            record_error,
        )


async def _user_email(context: AppContext, user_id: int) -> str | None:
    async with context.database.transaction() as session:
        return await session.scalar(
            select(UserModel.email).where(UserModel.id == user_id, UserModel.deleted_at.is_(None))
        )


@tasks.task(task_name=SEND_EMAIL_TASK, queue_name=stream_name(NOTIFICATION_QUEUE))
async def send_email(job: dict[str, Any], context: Annotated[AppContext, APP_CONTEXT]) -> None:
    notification_id = int(job["notificationId"])
    user_id = int(job["userId"])

    async def deliver() -> None:
        try:
            email = await _user_email(context, user_id)
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

    await run_job(NOTIFICATION_QUEUE, DEFAULT_RETRY, deliver)


async def queue_external_notifications(queue: TaskQueue, notification: NotificationModel) -> None:
    """按通知的渠道入队；SMS 与微信订阅消息尚无实现，跳过并记录日志。"""
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
        elif channel == "SMS" or (
            channel == "PUSH" and (notification.metadata_ or {}).get("wechatSubscribe")
        ):
            logger.warning(
                "Channel %s is not supported, skipped for notification %s",
                channel,
                notification.id,
            )
