"""纯 ASGI 中间件：Request ID 与语言、安全头；以及请求体上限计数。"""

import re
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException

from app.common.i18n import I18n, reset_language, set_language

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER: Final = "x-request-id"
_REQUEST_ID_RE: Final = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

CORS_ALLOWED_HEADERS: Final = (
    "Authorization",
    "Content-Type",
    "Accept-Language",
    "X-Request-Id",
    "Idempotency-Key",
    "traceparent",
    "tracestate",
)
CORS_EXPOSED_HEADERS: Final = (
    "X-Request-Id",
    "Retry-After",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
)
CORS_METHODS: Final = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")

SECURITY_HEADERS: Final = (
    (
        "Content-Security-Policy",
        "default-src 'self';base-uri 'self';font-src 'self' https: data:;form-action 'self';"
        "frame-ancestors 'self';img-src 'self' data: https:;object-src 'none';script-src 'self';"
        "script-src-attr 'none';style-src 'self' 'unsafe-inline';upgrade-insecure-requests",
    ),
    ("Cross-Origin-Resource-Policy", "cross-origin"),
    ("Origin-Agent-Cluster", "?1"),
    ("Referrer-Policy", "no-referrer"),
    ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-DNS-Prefetch-Control", "off"),
    ("X-Download-Options", "noopen"),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("X-Permitted-Cross-Domain-Policies", "none"),
    ("X-XSS-Protection", "0"),
)


def current_request_id() -> str | None:
    return _request_id.get()


def resolve_request_id(incoming: str | None) -> str:
    if incoming is not None and _REQUEST_ID_RE.fullmatch(incoming):
        return incoming
    return str(uuid.uuid7())


class RequestContextMiddleware:
    """沿用合法的入站 X-Request-Id，否则生成 UUIDv7，并回写响应头；按 Accept-Language 设定语言。"""

    def __init__(self, app: ASGIApp, i18n: I18n) -> None:
        self.app = app
        self.i18n = i18n

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        incoming = headers.getlist(REQUEST_ID_HEADER)
        request_id = resolve_request_id(incoming[0] if len(incoming) == 1 else None)
        if not incoming or incoming[0] != request_id:
            scope["headers"] = [
                (k, v) for k, v in scope["headers"] if k.lower() != REQUEST_ID_HEADER.encode()
            ] + [(REQUEST_ID_HEADER.encode(), request_id.encode())]
        token = _request_id.set(request_id)
        languages = headers.getlist("accept-language")
        language_token = set_language(self.i18n.resolve(", ".join(languages) or None))

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            _request_id.reset(token)
            reset_language(language_token)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class PayloadTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(413)


def declared_length(scope: Scope) -> int | None:
    raw = Headers(scope=scope).get("content-length")
    return int(raw) if raw is not None and raw.strip().isdigit() else None


def limit_body(scope: Scope, receive: Receive, limit: int) -> Receive:
    """按 Content-Length 与实际读取字节数检查上限，超出抛 413。"""
    length = declared_length(scope)
    if length is not None and length > limit:
        raise PayloadTooLarge
    received = 0

    async def limited() -> Message:
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                raise PayloadTooLarge
        return message

    return limited
