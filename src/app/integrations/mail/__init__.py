"""邮件：发送接口、只写日志的 LoggingMailer（保留最近消息供测试读取）、模板邮件组装与 SMTP。"""

import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import quote

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, StrictUndefined

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


def mask_email(address: str) -> str:
    local, at, domain = address.partition("@")
    if not at:
        return "***"
    return f"{local[:1]}***@{domain}"


@dataclass(frozen=True, slots=True)
class OutgoingMail:
    to: str
    subject: str | None
    text: str | None
    html: str | None


@dataclass(frozen=True, slots=True)
class _TemplateSpec:
    file: str
    title_key: str
    url_path: str
    text_keys: tuple[str, ...]


TEMPLATES: Final = {
    "activation": _TemplateSpec(
        "activation.html",
        "confirmEmail",
        "/confirm-email",
        ("confirm-email.text1", "confirm-email.text2", "confirm-email.text3"),
    ),
    "reset-password": _TemplateSpec(
        "reset-password.html",
        "resetPassword",
        "/password-change",
        (
            "reset-password.text1",
            "reset-password.text2",
            "reset-password.text3",
            "reset-password.text4",
        ),
    ),
    "confirm-new-email": _TemplateSpec(
        "confirm-new-email.html",
        "confirmEmail",
        "/confirm-new-email",
        ("confirm-new-email.text1", "confirm-new-email.text2", "confirm-new-email.text3"),
    ),
}

_HANDLEBARS_ESCAPES: Final = str.maketrans(
    {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#x27;",
        "`": "&#x60;",
        "=": "&#x3D;",
    }
)


def handlebars_escape(value: Any) -> str:
    """Handlebars `{{x}}` 的 escapeExpression。"""
    if value is None:
        return ""
    return str(value).translate(_HANDLEBARS_ESCAPES)


def _form_encode(value: str) -> str:
    """WHATWG URLSearchParams 序列化：空格为 `+`，仅 `*-._` 与字母数字不编码。"""
    return quote(value, safe="*-._").replace("~", "%7E").replace("%20", "+")


def _url_params(template: str, data: dict[str, Any]) -> dict[str, str]:
    params = {"hash": str(data["hash"])}
    if template == "reset-password":
        params["expires"] = str(data["tokenExpires"])
    return params


class MailComposer:
    """nestjs MailService：按语言取标题与正文，链接为 FRONTEND_DOMAIN 的第一个域名 + 路径。"""

    def __init__(self, app: AppSettings, i18n: I18n) -> None:
        self.app = app
        self.i18n = i18n
        self.environment = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=False,  # noqa: S701
            finalize=handlebars_escape,
            undefined=StrictUndefined,
        )

    def _t(self, key: str, lang: str | None) -> str:
        return self.i18n.translate(key, lang) or key

    def _url(self, path: str, params: dict[str, str]) -> str:
        if not self.app.frontend_domain:
            raise RuntimeError("FRONTEND_DOMAIN is required to build mail links")
        base = self.app.frontend_domain.split(",")[0].strip() + path
        query = "&".join(f"{_form_encode(k)}={_form_encode(v)}" for k, v in params.items())
        return f"{base}?{query}"

    def compose(self, message: MailMessage, lang: str | None) -> OutgoingMail:
        spec = TEMPLATES.get(message.template)
        if spec is None:
            raise ValueError(f"Unknown mail template {message.template}")
        title = self._t(f"common.{spec.title_key}", lang)
        url = self._url(spec.url_path, _url_params(message.template, message.data))
        texts = {f"text{i}": self._t(key, lang) for i, key in enumerate(spec.text_keys, 1)}
        html = self.environment.get_template(spec.file).render(
            title=title, url=url, actionTitle=title, app_name=self.app.name, **texts
        )
        return OutgoingMail(to=message.to, subject=title, text=f"{url} {title}", html=html)


class SmtpTransport:
    """nodemailer 语义：SECURE 隐式 TLS，否则可用时 STARTTLS；IGNORE_TLS 禁用，REQUIRE_TLS 强制。"""

    def __init__(self, settings: MailSettings) -> None:
        self.settings = settings

    def build(self, mail: OutgoingMail) -> EmailMessage:
        message = EmailMessage()
        message["From"] = formataddr(
            (self.settings.default_name or "", self.settings.default_email or "")
        )
        message["To"] = mail.to
        if mail.subject is not None:
            message["Subject"] = _LINE_BREAK.sub(" ", mail.subject)
        message["Date"] = format_datetime(datetime.now(UTC))
        domain = (self.settings.default_email or "").rpartition("@")[2] or "localhost"
        message["Message-ID"] = make_msgid(domain=domain)
        message.set_content(mail.text or "")
        if mail.html is not None:
            message.add_alternative(mail.html, subtype="html")
        return message

    async def send(self, mail: OutgoingMail) -> None:
        settings = self.settings
        start_tls: bool | None = None
        if settings.ignore_tls:
            start_tls = False
        elif settings.require_tls:
            start_tls = True
        await aiosmtplib.send(
            self.build(mail),
            hostname=settings.host,
            port=settings.port,
            username=settings.user,
            password=settings.password.get_secret_value() if settings.password else None,
            use_tls=settings.secure,
            start_tls=False if settings.secure else start_tls,
        )
