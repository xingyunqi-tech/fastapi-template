"""`GET /metrics`：Prometheus 拉取模式下挂载，`METRICS_TOKEN` Bearer 鉴权，不包信封、不进文档。"""

import hmac
import re
from typing import Final

from fastapi import Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.common.errors import AppError
from app.common.routing import AUTH_DEPENDENCIES, ApiRouter, Auth
from app.infrastructure.telemetry import REGISTRY
lazy from app.common.context import AppContext

_WHITESPACE: Final = re.compile(r"\s+")


def token_matches(authorization: str | None, expected: str) -> bool:
    parts = _WHITESPACE.split((authorization or "").strip(), maxsplit=2)
    credentials = parts[1] if len(parts) > 1 else ""
    return parts[0].lower() == "bearer" and hmac.compare_digest(
        credentials.encode(), expected.encode()
    )


async def monitoring_token(request: Request) -> None:
    """配置 `METRICS_TOKEN` 时校验 Bearer；未配置时仅非生产放行。"""
    context: AppContext = request.app.state.context
    token = context.settings.telemetry.metrics_token
    if token is None:
        allowed = not context.settings.app.is_production
    else:
        allowed = token_matches(request.headers.get("authorization"), token.get_secret_value())
    if not allowed:
        raise AppError("common.unauthorized")


AUTH_DEPENDENCIES[Auth.MONITORING] = monitoring_token

router = ApiRouter()


@router.endpoint(
    "GET",
    "/metrics",
    operation_id="getMetrics",
    auth=Auth.MONITORING,
    envelope=False,
    include_in_schema=False,
    rate_limit=(),
)
async def get_metrics() -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
