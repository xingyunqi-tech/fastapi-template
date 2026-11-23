"""邮件：发送接口、只写日志的 LoggingMailer（保留最近消息供测试读取）、模板渲染与 SMTP。"""

import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, parseaddr
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlencode

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.common.masking import mask_email
from app.config import MailTls
lazy from app.common.i18n import I18n
lazy from app.config import AppSettings, MailSettings

logger = logging.getLogger("app.mail")
RECENT_LIMIT: Final = 100
TEMPLATES_DIR: Final = Path(__file__).parent / "templates"
_LINE_BREAK: Final = re.compile(r"\r\n|[\r\n]")


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


@dataclass(frozen=True, slots=True)
class OutgoingMail:
    to: str
    subject: str
    text: str
    html: str


@dataclass(frozen=True, slots=True)
class _TemplateSpec:
    text_prefix: str
    path: str


TEMPLATES: Final = {
    "verify-email": _TemplateSpec("verifyEmail", "/verify-email"),
    "reset-password": _TemplateSpec("resetPassword", "/reset-password"),
}


class MailComposer:
    """链接为 `APP_FRONTEND_URL` + 路径 + `?token=`；`{app}` 为 `OTEL_SERVICE_NAME`。"""

    def __init__(self, app: AppSettings, i18n: I18n, app_name: str) -> None:
        self.app = app
        self.i18n = i18n
        self.app_name = app_name
        self.environment = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=select_autoescape(["html"]),
            undefined=StrictUndefined,
        )

    def _text(self, key: str, lang: str) -> str:
        text = self.i18n.translate(f"mail.{key}", lang) or key
        return text.replace("{app}", self.app_name)

    def link(self, template: str, token: str) -> str:
        if not self.app.frontend_url:
            raise RuntimeError("APP_FRONTEND_URL is required to build mail links")
        path = TEMPLATES[template].path
        return f"{self.app.frontend_url.rstrip('/')}{path}?{urlencode({'token': token})}"

    def compose(self, message: MailMessage, lang: str | None) -> OutgoingMail:
        spec = TEMPLATES.get(message.template)
        if spec is None:
            raise ValueError(f"Unknown mail template {message.template}")
        language = lang if lang in self.i18n.supported_languages else self.i18n.default_locale
        texts = {
            name: self._text(f"{spec.text_prefix}.{name}", language)
            for name in ("subject", "body", "action", "expires")
        }
        url = self.link(message.template, str(message.data["token"]))
        html = self.environment.get_template(f"{message.template}.html").render(
            lang=language, app_name=self.app_name, url=url, **texts
        )
        text = f"{texts['body']}\n\n{url}\n\n{texts['expires']}\n"
        return OutgoingMail(to=message.to, subject=texts["subject"], text=text, html=html)


class SmtpTransport:
    """`MAIL_TLS`：`tls` 隐式 TLS，`starttls` 强制 STARTTLS，`none` 明文。"""

    def __init__(self, settings: MailSettings) -> None:
        self.settings = settings

    def build(self, mail: OutgoingMail) -> EmailMessage:
        sender = self.settings.sender or ""
        message = EmailMessage()
        message["From"] = sender
        message["To"] = mail.to
        message["Subject"] = _LINE_BREAK.sub(" ", mail.subject)
        message["Date"] = format_datetime(datetime.now(UTC))
        domain = Address(addr_spec=parseaddr(sender)[1] or "app@localhost").domain or "localhost"
        message["Message-ID"] = make_msgid(domain=domain)
        message.set_content(mail.text)
        message.add_alternative(mail.html, subtype="html")
        return message

    async def send(self, mail: OutgoingMail) -> None:
        settings = self.settings
        await aiosmtplib.send(
            self.build(mail),
            hostname=settings.host,
            port=settings.port,
            username=settings.user,
            password=settings.password.get_secret_value() if settings.password else None,
            use_tls=settings.tls is MailTls.TLS,
            start_tls=settings.tls is MailTls.STARTTLS,
        )
