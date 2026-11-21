import asyncio
import json
import logging
from collections.abc import Awaitable
from typing import Any, Final

from starlette.responses import JSONResponse, Response

from app.common.context import Context
from app.common.routing import ApiRouter, Auth
from app.infrastructure.health import indicators
from app.infrastructure.health.indicators import IndicatorResult
from app.infrastructure.metrics.guard import monitoring_token as _monitoring_token  # noqa: F401

NO_CACHE: Final = {"Cache-Control": "no-cache, no-store, must-revalidate"}
logger = logging.getLogger("app.health")


async def run_checks(checks: list[Awaitable[IndicatorResult]]) -> Response:
    """全部 up 时 200 返回明细；任一项 down 时 503 只返回 `{"status": "error"}`，明细写日志。"""
    info: dict[str, Any] = {}
    error: dict[str, Any] = {}
    for result in await asyncio.gather(*checks):
        for key, value in result.items():
            (info if value["status"] == "up" else error)[key] = value
    if error:
        logger.error("Health Check has failed! %s", json.dumps(error, ensure_ascii=False))
        return JSONResponse({"status": "error"}, status_code=503, headers=NO_CACHE)
    body = {"status": "ok", "info": info, "error": error, "details": info}
    return JSONResponse(body, headers=NO_CACHE)


router = ApiRouter(prefix="/v1/health", tags=["Health"])


@router.endpoint(
    "GET",
    "",
    operation_id="healthCheck",
    auth=Auth.MONITORING,
    envelope=False,
    include_in_schema=False,
    summary="完整健康检查（需监控令牌）",
)
async def health_check(context: Context) -> Response:
    lock = asyncio.Lock()
    checks: list[Awaitable[IndicatorResult]] = [
        indicators.memory_heap("memory_heap"),
        indicators.disk("disk"),
        indicators.database_ping("database", context, lock),
        indicators.database_pool("database_pool", context, lock),
    ]
    if context.settings.redis.enabled:
        checks.append(indicators.redis("redis", context))
    checks.append(indicators.wechat_api("wechat_api", context))
    checks.append(indicators.alipay_api("alipay_api", context))
    return await run_checks(checks)


@router.endpoint(
    "GET",
    "/live",
    operation_id="livenessProbe",
    auth=Auth.PUBLIC,
    envelope=False,
    include_in_schema=False,
    summary="存活探针（Kubernetes）",
)
async def liveness() -> Response:
    return await run_checks([indicators.memory_heap("memory_heap")])


@router.endpoint(
    "GET",
    "/ready",
    operation_id="readinessProbe",
    auth=Auth.PUBLIC,
    envelope=False,
    include_in_schema=False,
    summary="就绪探针（Kubernetes）",
)
async def readiness(context: Context) -> Response:
    return await run_checks([indicators.database_ping("database", context, asyncio.Lock())])
