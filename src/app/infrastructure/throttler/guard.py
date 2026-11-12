"""nestjs UserThrottlerGuard：JWT 路由按 `user:{id}`、其余按 `ip:{ip}` 计数，键同 nestjs。"""

import hashlib
import json
import re
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from starlette.datastructures import MutableHeaders
lazy from fastapi import Request

from app.common.client import client_ip
from app.common.exceptions import TooManyRequests
from app.common.routing import ApiRoute, Auth, register_throttle_guard
from app.common.security import InvalidTokenError, Jwt, TokenExpiredError
from app.config import FileDriver
from app.infrastructure.throttler.ip import normalize_ip
from app.infrastructure.throttler.storage import THROTTLER_NAME, ThrottlerStorage
lazy from app.common.context import AppContext

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

NEST_HANDLERS_PATH: Final = Path(__file__).with_name("nest_handlers.json")
HEADERS_STATE_KEY: Final = "_rate_limit_headers"
_WHITESPACE: Final = re.compile(r"\s+")
UPLOAD_OPERATION: Final = "uploadFile"
UPLOAD_HANDLERS: Final = {
    FileDriver.LOCAL: "FilesLocalController-uploadFile",
    FileDriver.S3: "FilesS3Controller-uploadFile",
    FileDriver.S3_PRESIGNED: "FilesS3PresignedController-uploadFile",
}


@cache
def nest_handlers() -> dict[str, str]:
    """operationId → nestjs `Controller-handler`（由 nestjs 源码生成）。"""
    data: dict[str, str] = json.loads(NEST_HANDLERS_PATH.read_text(encoding="utf-8"))
    return data


def handler_name(route: ApiRoute, file_driver: FileDriver = FileDriver.LOCAL) -> str:
    """`uploadFile` 按 FILE_DRIVER 对应 nestjs 的三个上传 controller。"""
    operation_id = route.operation_id or route.name
    if operation_id == UPLOAD_OPERATION:
        return UPLOAD_HANDLERS[file_driver]
    return nest_handlers().get(operation_id, operation_id)


def throttle_key(route: ApiRoute, tracker: str, file_driver: FileDriver = FileDriver.LOCAL) -> str:
    raw = f"{handler_name(route, file_driver)}-{THROTTLER_NAME}-{tracker}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _bearer_user_id(request: Request, secret: str) -> Any:
    parts = _WHITESPACE.split((request.headers.get("authorization") or "").strip(), maxsplit=2)
    if parts[0].lower() != "bearer" or len(parts) < 2 or not parts[1]:
        return None
    try:
        return Jwt.verify(parts[1], secret).get("id")
    except InvalidTokenError, TokenExpiredError:
        return None


def _js_string(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def tracker_for(request: Request, route: ApiRoute, context: AppContext) -> str:
    if route.auth is Auth.JWT:
        user_id = _bearer_user_id(request, context.settings.auth.secret.get_secret_value())
        if user_id is not None:
            return f"user:{_js_string(user_id)}"
    ip = client_ip(request, context.settings.app.trust_proxy) or ""
    return f"ip:{normalize_ip(ip)}"


async def throttle_guard(request: Request) -> None:
    context: AppContext = request.app.state.context
    settings = context.settings.throttler
    route = request.scope.get("route")
    storage: ThrottlerStorage | None = getattr(request.app.state, "throttler", None)
    if not settings.enabled or not isinstance(route, ApiRoute) or storage is None:
        return
    limit = route.throttle.limit if route.throttle else settings.limit
    ttl_ms = route.throttle.ttl if route.throttle else settings.ttl_ms
    tracker = tracker_for(request, route, context)
    key = throttle_key(route, tracker, context.settings.file.driver)
    decision = await storage.consume(context.redis, key, ttl_ms, limit)
    if not decision.allowed:
        raise TooManyRequests({"Retry-After": str(decision.retry_after_seconds)})
    pending = request.scope.get("state", {}).get(HEADERS_STATE_KEY)
    if pending is not None:
        pending["X-RateLimit-Limit"] = str(limit)
        pending["X-RateLimit-Remaining"] = str(decision.remaining)
        pending["X-RateLimit-Reset"] = str(decision.reset_seconds)


register_throttle_guard(throttle_guard)


class RateLimitHeadersMiddleware:
    """把限流守卫算出的 X-RateLimit-* 写到最终响应（含守卫之后抛出的错误响应）。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        pending: dict[str, str] = {}
        scope.setdefault("state", {})[HEADERS_STATE_KEY] = pending

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start" and pending:
                headers = MutableHeaders(scope=message)
                for name, value in pending.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
