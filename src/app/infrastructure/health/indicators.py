"""@nestjs/terminus 健康指示器的移植：每项返回 `{key: {...data, status}}`。"""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx
import psutil  # type: ignore[import-untyped]
from sqlalchemy import text

lazy from app.common.context import AppContext

type IndicatorResult = dict[str, dict[str, Any]]
type Indicator = Callable[[], Awaitable[IndicatorResult]]

HEAP_THRESHOLD_BYTES: Final = 300 * 1024 * 1024
DISK_THRESHOLD_PERCENT: Final = 0.9
DATABASE_PING_TIMEOUT_MS: Final = 1000
POOL_UTILIZATION_THRESHOLD: Final = 90
HTTP_TIMEOUT_S: Final = 5.0
REDIS_TIMEOUT_S: Final = 5.0
WECHAT_API_URL: Final = "https://api.weixin.qq.com/cgi-bin/getcallbackip"
ALIPAY_GATEWAY_URL: Final = "https://openapi.alipay.com/gateway.do"
POOL_STATS_SQL: Final = text(
    """
    SELECT
      numbackends as active_connections,
      (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') as max_connections
    FROM pg_stat_database
    WHERE datname = current_database()
    """
)


def up(key: str, data: dict[str, Any] | None = None) -> IndicatorResult:
    return {key: {**(data or {}), "status": "up"}}


def down(key: str, data: dict[str, Any] | None = None) -> IndicatorResult:
    return {key: {**(data or {}), "status": "down"}}


def _message(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _js_round(value: float) -> int:
    return math.floor(value + 0.5)


def storage_exceeded(keyword: str) -> dict[str, Any]:
    return {"message": f"Used {keyword} exceeded the set threshold"}


async def memory_heap(key: str) -> IndicatorResult:
    """进程 RSS 超过 300MB 视为不健康；nestjs `checkHeap` 比较的是 V8 heapUsed，两者不等价。"""
    if psutil.Process().memory_info().rss > HEAP_THRESHOLD_BYTES:
        return down(key, storage_exceeded("heap"))
    return up(key)


async def disk(key: str, path: str = "/") -> IndicatorResult:
    usage = await asyncio.to_thread(psutil.disk_usage, path)
    if not usage.total:
        return down(key, storage_exceeded("disk storage"))
    used = usage.total - usage.free
    if used / usage.total > DISK_THRESHOLD_PERCENT:
        return down(key, storage_exceeded("disk storage"))
    return up(key)


async def database_ping(key: str, context: AppContext, lock: asyncio.Lock) -> IndicatorResult:
    started = time.perf_counter()
    try:
        async with (
            lock,
            asyncio.timeout(DATABASE_PING_TIMEOUT_MS / 1000),
            context.database.sessionmaker() as session,
        ):
            await session.execute(text("SELECT 1"))
    except TimeoutError:
        message = f"timeout of {DATABASE_PING_TIMEOUT_MS}ms exceeded"
        elapsed = _js_round((time.perf_counter() - started) * 1000)
        return down(key, {"message": message, "responseTime": elapsed})
    except Exception as error:
        elapsed = _js_round((time.perf_counter() - started) * 1000)
        return down(key, {"message": _message(error), "responseTime": elapsed})
    return up(key, {"responseTime": _js_round((time.perf_counter() - started) * 1000)})


async def database_pool(key: str, context: AppContext, lock: asyncio.Lock) -> IndicatorResult:
    try:
        async with lock, context.database.sessionmaker() as session:
            row = (await session.execute(POOL_STATS_SQL)).first()
        if row is None:
            raise RuntimeError("Unable to retrieve database pool statistics")
        active = int(row.active_connections)
        maximum = int(row.max_connections)
        stats = {
            "activeConnections": active,
            "maxConnections": maximum,
            "utilizationPercent": _js_round(active / maximum * 100),
        }
    except Exception as error:
        return down(key, {"message": _message(error)})
    if stats["utilizationPercent"] > POOL_UTILIZATION_THRESHOLD:
        return down(key, stats)
    return up(key, stats)


async def redis(key: str, context: AppContext) -> IndicatorResult:
    if not context.settings.redis.enabled:
        return up(key, {"message": "Redis disabled"})
    client = context.redis
    try:
        if client is None:
            raise RuntimeError("Redis client not initialized")
        async with asyncio.timeout(REDIS_TIMEOUT_S):
            pong = await client.ping()
    except Exception as error:
        return down(key, {"message": _message(error)})
    return up(key) if pong else down(key)


def _fetch_error(error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "This operation was aborted"
    return "fetch failed"


async def wechat_api(key: str, context: AppContext) -> IndicatorResult:
    settings = context.settings.wechat
    if not settings.app_id or not settings.app_secret.get_secret_value():
        return up(key, {"message": "WeChat not configured, skipped"})
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_S, transport=context.http_transport
        ) as client:
            response = await client.get(WECHAT_API_URL)
    except httpx.HTTPError as error:
        return down(key, {"message": _fetch_error(error)})
    if response.is_success:
        return up(key, {"responseTime": "OK"})
    return down(key, {"statusCode": response.status_code})


async def alipay_api(key: str, context: AppContext) -> IndicatorResult:
    if not context.settings.alipay.app_id:
        return up(key, {"message": "Alipay not configured, skipped"})
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_S, transport=context.http_transport
        ) as client:
            response = await client.head(ALIPAY_GATEWAY_URL)
    except httpx.HTTPError as error:
        return down(key, {"message": _fetch_error(error)})
    if response.is_success or response.status_code == 405:
        return up(key, {"responseTime": "OK"})
    return down(key, {"statusCode": response.status_code})
