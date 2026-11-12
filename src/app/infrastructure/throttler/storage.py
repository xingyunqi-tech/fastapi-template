"""GCRA 限流存储：Redis Lua 原子执行，Redis 未启用或故障时回落进程内存。"""

import asyncio
import heapq
import logging
import math
import time
from dataclasses import dataclass
from typing import Final

lazy from redis.asyncio import Redis

from app.infrastructure.alerting import AlertLevel, AlertReporter
from app.infrastructure.metrics import throttler_storage_fallback_total

logger = logging.getLogger("app.throttler")

KEY_PREFIX: Final = "throttle:"
THROTTLER_NAME: Final = "default"
GCRA_KEY_SUFFIX: Final = ":gcra"
COMMAND_TIMEOUT_S: Final = 0.5
THROTTLER_FALLBACK_ALERT_KEY: Final = "throttler-storage-fallback"
MEMORY_MAX_KEYS: Final = 10_000

GCRA_SCRIPT: Final = """
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) * 1000 + tonumber(now_parts[2]) / 1000
local period = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local interval = period / limit
local tat = tonumber(redis.call('GET', KEYS[1])) or now
if tat < now then
  tat = now
end
local new_tat = tat + interval
local allow_at = new_tat - period
if allow_at > now then
  return {0, 0, math.ceil(tat - now), math.ceil(allow_at - now)}
end
redis.call('SET', KEYS[1], tostring(new_tat), 'PX', math.ceil(new_tat - now))
local remaining = math.floor((period - (new_tat - now)) / interval + 1e-9)
return {1, remaining, math.ceil(new_tat - now), 0}
"""


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    remaining: int
    reset_ms: int
    retry_after_ms: int

    @property
    def reset_seconds(self) -> int:
        return math.ceil(self.reset_ms / 1000)

    @property
    def retry_after_seconds(self) -> int:
        return max(1, math.ceil(self.retry_after_ms / 1000))


def redis_key(key: str) -> str:
    """nestjs 计数键 `throttle:{sha}:default` 加 `:gcra` 后缀，与其 INCR 计数互不干扰。"""
    return f"{KEY_PREFIX}{key}:{THROTTLER_NAME}{GCRA_KEY_SUFFIX}"


class MemoryGcra:
    def __init__(self) -> None:
        self._tat: dict[str, float] = {}

    def consume(self, key: str, ttl_ms: int, limit: int, now: float | None = None) -> Decision:
        now = time.monotonic() * 1000 if now is None else now
        interval = ttl_ms / limit
        tat = max(self._tat.get(key, now), now)
        new_tat = tat + interval
        allow_at = new_tat - ttl_ms
        if allow_at > now:
            return Decision(False, 0, math.ceil(tat - now), math.ceil(allow_at - now))
        self._store(key, new_tat, now)
        remaining = math.floor((ttl_ms - (new_tat - now)) / interval + 1e-9)
        return Decision(True, remaining, math.ceil(new_tat - now), 0)

    def _store(self, key: str, tat: float, now: float) -> None:
        """键满时先清掉已过期的，仍满则淘汰 TAT 最小（最早过期）的键。"""
        if key not in self._tat and len(self._tat) >= MEMORY_MAX_KEYS:
            self._tat = {k: v for k, v in self._tat.items() if v > now}
            excess = len(self._tat) - MEMORY_MAX_KEYS + 1
            if excess > 0:
                for expiring in heapq.nsmallest(excess, self._tat, key=self._tat.__getitem__):
                    del self._tat[expiring]
        self._tat[key] = tat

    def clear(self) -> None:
        self._tat.clear()


class ThrottlerStorage:
    """nestjs RedisThrottlerStorage：Redis 出错时计入回落指标并改用内存。"""

    def __init__(self, alerts: AlertReporter | None = None) -> None:
        self.memory = MemoryGcra()
        self.degraded = False
        self.alerts = alerts
        self._pending: set[asyncio.Task[None]] = set()

    async def consume(self, redis: Redis | None, key: str, ttl_ms: int, limit: int) -> Decision:
        if redis is not None:
            try:
                async with asyncio.timeout(COMMAND_TIMEOUT_S):
                    raw = await redis.eval(GCRA_SCRIPT, 1, redis_key(key), ttl_ms, limit)
            except Exception as error:
                self._mark_degraded(error)
            else:
                self._mark_recovered()
                allowed, remaining, reset_ms, retry_ms = (int(v) for v in raw)
                return Decision(allowed == 1, remaining, reset_ms, retry_ms)
        return self.memory.consume(key, ttl_ms, limit)

    def _mark_degraded(self, error: BaseException) -> None:
        throttler_storage_fallback_total.inc()
        if self.degraded:
            return
        self.degraded = True
        reason = str(error) or type(error).__name__
        logger.error(
            "Redis throttler storage unavailable, falling back to in-memory storage: %s", reason
        )
        if self.alerts is not None:
            task = asyncio.create_task(self._raise_alert(self.alerts, reason))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

    @staticmethod
    async def _raise_alert(alerts: AlertReporter, reason: str) -> None:
        try:
            await alerts.raise_(
                "throttler",
                AlertLevel.CRITICAL,
                f"限流存储 Redis 不可用，已降级为单实例内存计数：{reason}",
                {"reason": reason},
                THROTTLER_FALLBACK_ALERT_KEY,
            )
        except Exception:
            logger.exception("Failed to raise throttler fallback alert")

    def _mark_recovered(self) -> None:
        if self.degraded:
            self.degraded = False
            logger.info("Redis throttler storage recovered")
