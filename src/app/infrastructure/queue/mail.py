"""mail 队列：请求侧记录模板、收件人与当前语言后入队，worker 渲染模板并经 SMTP 发送。"""

import asyncio
import logging
from typing import Annotated, Any, Final

from app.common.i18n import current_language, get_i18n
from app.infrastructure.queue import (
    APP_CONTEXT,
    DEFAULT_RETRY,
    MAIL_QUEUE,
    run_job,
    stream_name,
    tasks,
)
from app.integrations.mail import MailComposer, MailMessage, SmtpTransport, mask_email
lazy from app.common.context import AppContext
lazy from app.config import Settings
lazy from app.infrastructure.queue import TaskQueue

logger = logging.getLogger("app.queue.mail")

SEND_MAIL_TASK: Final = "mail:send-mail"
ENQUEUE_TIMEOUT_S: Final = 2.0


async def deliver_mail(job: dict[str, Any], settings: Settings) -> None:
    """渲染模板并经 SMTP 发送，失败按 DEFAULT_RETRY 重试。"""
    message = MailMessage(job["template"], job["to"], job.get("data") or {})
    composer = MailComposer(settings.app, get_i18n(settings.app.fallback_language))
    transport = SmtpTransport(settings.mail)

    async def deliver() -> None:
        await transport.send(composer.compose(message, job.get("lang")))

    logger.info("Processing mail %s to %s", message.template, mask_email(message.to))
    await run_job(MAIL_QUEUE, DEFAULT_RETRY, deliver)


@tasks.task(task_name=SEND_MAIL_TASK, queue_name=stream_name(MAIL_QUEUE))
async def send_mail(job: dict[str, Any], context: Annotated[AppContext, APP_CONTEXT]) -> None:
    await deliver_mail(job, context.settings)


class QueueMailer:
    """入队超时或失败时改在本进程后台直接发送。"""

    def __init__(self, queue: TaskQueue, settings: Settings) -> None:
        self.queue = queue
        self.settings = settings
        self.pending: set[asyncio.Task[None]] = set()

    async def send(self, message: MailMessage) -> None:
        job = {
            "template": message.template,
            "to": message.to,
            "data": message.data,
            "lang": current_language(),
        }
        try:
            async with asyncio.timeout(ENQUEUE_TIMEOUT_S):
                await self.queue.enqueue(send_mail, job)
        except Exception as error:
            logger.error(
                "Failed to enqueue mail to %s, sending in process: %r",
                mask_email(message.to),
                error.__cause__ or error,
            )
            task = asyncio.create_task(self._deliver_in_process(job))
            self.pending.add(task)
            task.add_done_callback(self.pending.discard)
            return
        logger.info("Mail job added to queue: %s", mask_email(message.to))

    async def _deliver_in_process(self, job: dict[str, Any]) -> None:
        try:
            await deliver_mail(job, self.settings)
        except Exception:
            logger.exception("In-process delivery of mail to %s failed", mask_email(job["to"]))

    async def drain(self) -> None:
        await asyncio.gather(*self.pending, return_exceptions=True)
