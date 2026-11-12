"""纯 ASGI 中间件：Request ID 与语言、CORS（cors 2.8 语义）、安全头（nestjs helmet）、兜底 500。"""

import logging
import re
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final
lazy from collections.abc import Sequence

from starlette.datastructures import Headers, MutableHeaders

from app.common.exceptions import HttpError
from app.common.i18n import I18n, reset_language, set_language
from app.common.responses import JsonResponse
from app.common.validation.js import UNDEFINED, JsValue, parse_json
lazy from app.common.errors import ErrorTranslator

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.http")

REQUEST_ID_HEADER: Final = "x-request-id"
_REQUEST_ID_RE: Final = re.compile(r"[\w.:-]{1,128}", re.ASCII)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

DEFAULT_CORS_METHODS: Final = "GET,HEAD,PUT,PATCH,POST,DELETE"
CORS_EXPOSED_HEADERS: Final = (
    "X-Request-Id",
    "Retry-After",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
)

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
    return str(uuid.uuid4())


class RequestContextMiddleware:
    """沿用合法的入站 X-Request-Id 或生成 UUID 并回写响应头；按 Accept-Language 设定请求语言。"""

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


def _vary(headers: MutableHeaders, field: str) -> None:
    current = headers.get("vary")
    if current is None:
        headers["Vary"] = field
        return
    if current.strip() == "*":
        return
    existing = [v.strip().lower() for v in current.split(",")]
    additions = [f for f in (v.strip() for v in field.split(",")) if f.lower() not in existing]
    if "*" in (a for a in additions):
        headers["Vary"] = "*"
    elif additions:
        headers["Vary"] = ", ".join([current, *additions])


class CorsMiddleware:
    """cors@2.8：`origins` 为 None 时回显来源与请求头，否则按白名单；OPTIONS 一律 204。"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        origins: Sequence[str] | None,
        methods: str = DEFAULT_CORS_METHODS,
        allowed_headers: str | None = None,
        exposed_headers: Sequence[str] = CORS_EXPOSED_HEADERS,
    ) -> None:
        self.app = app
        self.origins = list(origins) if origins is not None else None
        self.methods = methods
        self.allowed_headers = allowed_headers
        self.exposed_headers = ",".join(exposed_headers)

    def _apply_origin(self, headers: MutableHeaders, origin: str | None) -> None:
        allowed = self.origins is None or (origin is not None and origin in self.origins)
        if allowed and origin:
            headers["Access-Control-Allow-Origin"] = origin
        _vary(headers, "Origin")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_headers = Headers(scope=scope)
        origin = request_headers.get("origin")
        if scope["method"].upper() == "OPTIONS":
            headers = MutableHeaders()
            self._apply_origin(headers, origin)
            headers["Access-Control-Allow-Credentials"] = "true"
            headers["Access-Control-Allow-Methods"] = self.methods
            allowed = self.allowed_headers
            if allowed is None:
                allowed = request_headers.get("access-control-request-headers")
                _vary(headers, "Access-Control-Request-Headers")
            if allowed:
                headers["Access-Control-Allow-Headers"] = allowed
            headers["Access-Control-Expose-Headers"] = self.exposed_headers
            headers["Content-Length"] = "0"
            await send({"type": "http.response.start", "status": 204, "headers": headers.raw})
            await send({"type": "http.response.body", "body": b""})
            return

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                self._apply_origin(headers, origin)
                headers["Access-Control-Allow-Credentials"] = "true"
                headers["Access-Control-Expose-Headers"] = self.exposed_headers
            await send(message)

        await self.app(scope, receive, send_with_cors)


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


class CatchAllMiddleware:
    """把异常处理器未接住的异常转为错误信封。"""

    def __init__(self, app: ASGIApp, translator: ErrorTranslator) -> None:
        self.app = app
        self.translator = translator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            if started:
                raise
            body = self.translator.build(exc)
            extra = {"path": scope.get("path"), "request_id": current_request_id()}
            if body.status >= 500:
                logger.exception("%s: %s", body.log_error, body.message, extra=extra)
            else:
                logger.warning("%s: %s", body.log_error, body.message, extra=extra)
            response = JsonResponse(body.payload(), status_code=body.status, headers=body.headers)
            await response(scope, receive, send)


JSON_BODY_LIMIT: Final = 256 * 1024
JSON_BODY_STATE_KEY: Final = "_parsed_json_body"
_JSON_WHITESPACE: Final = " \t\n\r"


def _is_json(content_type: str | None) -> bool:
    if not content_type:
        return False
    return content_type.split(";", 1)[0].strip().lower() == "application/json"


def parse_json_body(raw: bytes) -> JsValue:
    """body-parser json（strict）：空体为 {}；首个非空白字符须为 { 或 [，否则 400。"""
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="replace").removeprefix("\ufeff")
    stripped = text.lstrip(_JSON_WHITESPACE)
    if stripped[:1] not in {"{", "["}:
        token = stripped[:1] or "end of input"
        raise HttpError(400, f"Unexpected token {token!r}, {text[:32]!r} is not valid JSON")
    try:
        return parse_json(text)
    except ValueError as exc:
        raise HttpError(400, str(exc)) from None


class JsonBodyMiddleware:
    """在路由匹配前解析 application/json 请求体（与 Express 全局 body-parser 一致）。"""

    def __init__(self, app: ASGIApp, translator: ErrorTranslator) -> None:
        self.app = app
        self.translator = translator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_json(Headers(scope=scope).get("content-type")):
            await self.app(scope, receive, send)
            return
        try:
            raw = await self._read(scope, receive)
            value = parse_json_body(raw)
        except HttpError as exc:
            body = self.translator.build(exc)
            logger.warning("%s: %s", body.log_error, body.message)
            await JsonResponse(body.payload(), status_code=body.status)(scope, receive, send)
            return
        scope.setdefault("state", {})[JSON_BODY_STATE_KEY] = value
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _read(scope: Scope, receive: Receive) -> bytes:
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > JSON_BODY_LIMIT:
            raise HttpError(413, "request entity too large")
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > JSON_BODY_LIMIT:
                raise HttpError(413, "request entity too large")
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        return b"".join(chunks)


class RawPathMiddleware:
    """路由按原始（百分号编码）路径匹配，与 Express 一致；路径参数由 ApiRoute 解码。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        raw_path = scope.get("raw_path")
        if scope["type"] == "http" and raw_path:
            scope = {**scope, "path": raw_path.decode("latin-1")}
        await self.app(scope, receive, send)


def parsed_json_body(scope: Scope) -> JsValue:
    return scope.get("state", {}).get(JSON_BODY_STATE_KEY, UNDEFINED)
