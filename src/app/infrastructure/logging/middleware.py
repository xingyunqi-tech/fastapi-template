"""pino-http 自动请求日志：`<METHOD> <url> <status>`，≥500 error、≥400 warn，其余 info。"""

import re
import time
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qsl

from starlette.datastructures import Headers

from app.common.middleware import current_request_id
from app.common.security import InvalidTokenError, Jwt, TokenExpiredError
from app.infrastructure.logging import bind_user_id, get_logger, reset_user_id

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = get_logger("app.http.access")
_WHITESPACE: Final = re.compile(r"\s+")


def level_for(status: int, failed: bool = False) -> str:
    if status >= 500 or failed:
        return "error"
    if status >= 400:
        return "warning"
    return "info"


def _user_id(scope: Scope, authorization: str | None) -> Any:
    parts = _WHITESPACE.split((authorization or "").strip(), maxsplit=2)
    if parts[0].lower() != "bearer" or len(parts) < 2:
        return None
    context = getattr(getattr(scope.get("app"), "state", None), "context", None)
    if context is None:
        return None
    try:
        return Jwt.verify(parts[1], context.settings.auth.secret.get_secret_value()).get("id")
    except InvalidTokenError, TokenExpiredError:
        return None


def _original_url(scope: Scope) -> str:
    raw_path = scope.get("raw_path")
    path = raw_path.decode("latin-1") if raw_path else scope["path"]
    query = scope.get("query_string", b"").decode("latin-1")
    return f"{path}?{query}" if query else path


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

        error: BaseException | None = None
        try:
            await self.app(scope, receive, tracking_send)
        except BaseException as exc:
            error = exc
            raise
        finally:
            self._log(scope, headers, status, started, error)
            reset_user_id(token)

    @staticmethod
    def _log(
        scope: Scope,
        headers: Headers,
        status: int,
        started: float,
        error: BaseException | None,
    ) -> None:
        method = scope["method"]
        url = _original_url(scope)
        client = scope.get("client")
        message = f"{method} {url} {status}"
        if error is not None:
            message = f"{message} - {error}"
        logger.log(
            {"error": 40, "warning": 30, "info": 20}[level_for(status, error is not None)],
            message,
            context="HTTP",
            req={
                "id": current_request_id() or headers.get("x-request-id"),
                "method": method,
                "url": url,
                "query": dict(parse_qsl(scope.get("query_string", b"").decode("latin-1"))),
                "headers": dict(headers.items()),
                "remoteAddress": client[0] if client else None,
            },
            res={"statusCode": status},
            responseTime=round((time.perf_counter() - started) * 1000),
        )
