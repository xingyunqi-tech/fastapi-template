"""约定 §1：`/health/live` 恒 200；`/health/ready` 检查已配置的数据库与 Redis，失败 503。"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, Final

from sqlalchemy import text
from starlette.responses import JSONResponse, Response

from app.common.context import AppContext, Context
from app.common.routing import ApiRouter, Auth

CHECK_TIMEOUT_S: Final = 2.0
RETRY_AFTER_S: Final = "5"
NO_CACHE: Final = {"Cache-Control": "no-store"}

logger = logging.getLogger("app.health")


async def _database(context: AppContext) -> None:
    async with context.db.sessionmaker() as session:
        await session.execute(text("SELECT 1"))


async def _redis(context: AppContext) -> None:
    if context.redis is not None:
        await context.redis.ping()


async def _check(name: str, check: Coroutine[Any, Any, None]) -> str | None:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_S):
            await check
    except Exception as error:
        logger.error("Readiness check %s failed: %s", name, str(error) or type(error).__name__)
        return name
    return None


router = ApiRouter(prefix="/health", tags=["Health"])


@router.endpoint(
    "GET",
    "/live",
    operation_id="getLiveness",
    auth=Auth.PUBLIC,
    envelope=False,
    include_in_schema=False,
    rate_limit=(),
)
async def liveness() -> Response:
    return JSONResponse({"status": "ok"}, headers=NO_CACHE)


@router.endpoint(
    "GET",
    "/ready",
    operation_id="getReadiness",
    auth=Auth.PUBLIC,
    envelope=False,
    include_in_schema=False,
    rate_limit=(),
)
async def readiness(context: Context) -> Response:
    checks = []
    if context.database is not None:
        checks.append(_check("database", _database(context)))
    if context.redis is not None:
        checks.append(_check("redis", _redis(context)))
    failed = [name for name in await asyncio.gather(*checks) if name is not None]
    if failed:
        return JSONResponse(
            {"status": "error"},
            status_code=503,
            headers={**NO_CACHE, "Retry-After": RETRY_AFTER_S},
        )
    return JSONResponse({"status": "ok"}, headers=NO_CACHE)
