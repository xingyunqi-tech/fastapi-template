"""nestjs MetricsMiddleware：响应结束时按 method / 路由模板 / 状态码记录请求数与耗时。"""

import re
import time
from typing import TYPE_CHECKING, Final

from starlette.routing import Match, Route

from app.infrastructure.metrics import (
    UNMATCHED_ROUTE,
    http_active_connections,
    http_request_duration_seconds,
    http_requests_total,
)

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

CLIENT_CLOSED_REQUEST: Final = 499
_PARAM_RE: Final = re.compile(r"\{([^}:]+)(?::[^}]*)?\}")


def express_path(path_format: str) -> str:
    return _PARAM_RE.sub(r":\1", path_format)


def route_label(scope: Scope) -> str:
    app = scope.get("app")
    router = getattr(app, "router", None)
    if router is None:
        return UNMATCHED_ROUTE
    raw_path = scope.get("raw_path")
    probe = {**scope, "path": raw_path.decode("latin-1") if raw_path else scope["path"]}
    for route in router.routes:
        if isinstance(route, Route):
            match, _ = route.matches(probe)
            if match is Match.FULL:
                return express_path(route.path_format)
    return UNMATCHED_ROUTE


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = CLIENT_CLOSED_REQUEST
        response_status = 0
        http_active_connections.inc()

        async def recording_send(message: Message) -> None:
            nonlocal status, response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                status = response_status
            await send(message)

        try:
            await self.app(scope, receive, recording_send)
        finally:
            http_active_connections.dec()
            labels = {"method": scope["method"], "path": route_label(scope), "status": str(status)}
            http_requests_total.labels(**labels).inc()
            http_request_duration_seconds.labels(**labels).observe(time.perf_counter() - started)
