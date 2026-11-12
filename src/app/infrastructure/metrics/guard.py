"""nestjs MonitoringTokenGuard：配置 METRICS_TOKEN 时校验 Bearer 令牌，未配置时仅非生产放行。"""

import hmac
import re
from typing import Final

lazy from fastapi import Request

from app.common.exceptions import forbidden
from app.common.routing import AUTH_DEPENDENCIES, Auth
lazy from app.common.context import AppContext

FORBIDDEN_MESSAGE: Final = "Forbidden resource"
_WHITESPACE: Final = re.compile(r"\s+")


def token_matches(authorization: str | None, expected: str) -> bool:
    parts = _WHITESPACE.split((authorization or "").strip(), maxsplit=2)
    credentials = parts[1] if len(parts) > 1 else ""
    return parts[0].lower() == "bearer" and hmac.compare_digest(
        credentials.encode(), expected.encode()
    )


async def monitoring_token(request: Request) -> None:
    context: AppContext = request.app.state.context
    token = context.settings.metrics.token
    if token is None:
        allowed = not context.settings.app.is_production
    else:
        allowed = token_matches(request.headers.get("authorization"), token.get_secret_value())
    if not allowed:
        raise forbidden(FORBIDDEN_MESSAGE)


AUTH_DEPENDENCIES[Auth.MONITORING] = monitoring_token
