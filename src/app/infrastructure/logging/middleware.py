"""访问日志（约定 §8）：≥500 error、≥400 warn，其余 info。"""

import logging
import re
import time
from typing import TYPE_CHECKING, Any, Final

from starlette.datastructures import Headers

from app.infrastructure.logging import bind_user_id, get_logger, reset_user_id

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = get_logger("app.http.access")
_WHITESPACE: Final = re.compile(r"\s+")


def level_for(status: int, failed: bool = False) -> int:
    if status >= 500 or failed:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    return logging.INFO


def _user_id(scope: Scope, authorization: str | None) -> Any:
    parts = _WHITESPACE.split((authorization or "").strip(), maxsplit=2)
    if parts[0].lower() != "bearer" or len(parts) < 2:
        return None
    context = getattr(getattr(scope.get("app"), "state", None), "context", None)
    if context is None:
        return None
    return context.verifier.subject(parts[1])


def route_template(scope: Scope) -> str | None:
    path = getattr(scope.get("route"), "path", None)
    return path if isinstance(path, str) else None


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        token = bind_user_id(_user_id(scope, headers.get("authorization")))
        started = time.perf_counter()
        status = 500

        async def tracking_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        failed = False
        try:
            await self.app(scope, receive, tracking_send)
        except BaseException:
            failed = True
            raise
        finally:
            logger.log(
                level_for(status, failed),
                "http request",
                method=scope["method"],
                route=route_template(scope),
                status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            reset_user_id(token)
