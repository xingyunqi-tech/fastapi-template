"""邮件发送接口与只写日志的 LoggingMailer（保留最近的消息供测试读取）。"""

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Final, Protocol

logger = logging.getLogger("app.mail")
RECENT_LIMIT: Final = 100


@dataclass(frozen=True, slots=True)
class MailMessage:
    template: str
    to: str
    data: dict[str, Any]


class Mailer(Protocol):
    async def send(self, message: MailMessage) -> None: ...


class LoggingMailer:
    def __init__(self) -> None:
        self.sent: deque[MailMessage] = deque(maxlen=RECENT_LIMIT)

    async def send(self, message: MailMessage) -> None:
        self.sent.append(message)
        logger.info("mail %s queued for %s", message.template, mask_email(message.to))


def mask_email(address: str) -> str:
    local, at, domain = address.partition("@")
    if not at:
        return "***"
    return f"{local[:1]}***@{domain}"
