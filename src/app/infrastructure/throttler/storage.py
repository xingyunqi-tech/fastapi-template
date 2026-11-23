"""GCRA：键 `rl:{规则名}:{主体}`，值为 TAT（Unix 毫秒整数字符串），`PX` 为 `TAT - now`。"""

import asyncio
import math
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

lazy from redis.asyncio import Redis

lazy from app.config import Quota

KEY_PREFIX: Final = "rl:"
MEMORY_MAX_KEYS: Final = 10_000
REDIS_TIMEOUT_S: Final = 0.5

GCRA_SCRIPT: Final = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local interval = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local tat = tonumber(redis.call('GET', KEYS[1]) or '0')
if tat < now then tat = now end
local limit_at = now + interval * burst
local next_tat = tat + interval
if next_tat > limit_at then
  return {0, 0, math.ceil(tat - now), math.ceil(next_tat - limit_at)}
end
if ARGV[3] == 'peek' then
  return {1, math.floor((limit_at - tat) / interval), math.ceil(tat - now), 0}
end
redis.call('SET', KEYS[1], string.format('%.0f', next_tat), 'PX', next_tat - now)
return {1, math.floor((limit_at - next_tat) / interval), next_tat - now, 0}
"""


class Mode(StrEnum):
    HIT = "hit"
    PEEK = "peek"
    """只判断能否通过，不计数。"""


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset_ms: int
    retry_after_ms: int

    @property
    def reset_seconds(self) -> int:
        return math.ceil(self.reset_ms / 1000)

    @property
    def retry_after_seconds(self) -> int:
        return max(1, math.ceil(self.retry_after_ms / 1000))


def key(rule: str, subject: str) -> str:
    return f"{KEY_PREFIX}{rule}:{subject}"


class GcraStore(Protocol):
    async def consume(self, key: str, quota: Quota, mode: Mode) -> Decision: ...

    async def reset(self, key: str) -> None: ...


def gcra(tat: int, now: int, quota: Quota, mode: Mode) -> tuple[Decision, int | None]:
    """返回判定结果与需要写入的新 TAT（拒绝或 `peek` 时为 None）。"""
    interval = quota.emission_ms
    tat = max(tat, now)
    limit_at = now + interval * quota.burst
    next_tat = tat + interval
    if next_tat > limit_at:
        return Decision(False, quota.burst, 0, tat - now, next_tat - limit_at), None
    if mode is Mode.PEEK:
        return Decision(True, quota.burst, (limit_at - tat) // interval, tat - now, 0), None
    remaining = (limit_at - next_tat) // interval
    return Decision(True, quota.burst, remaining, next_tat - now, 0), next_tat


class MemoryGcraStore:
    """`REDIS_URL` 未配置时的进程内实现。"""

    def __init__(self) -> None:
        self._tat: dict[str, int] = {}

    async def consume(self, key: str, quota: Quota, mode: Mode) -> Decision:
        return self.consume_at(key, quota, mode, time.time_ns() // 1_000_000)

    def consume_at(self, key: str, quota: Quota, mode: Mode, now: int) -> Decision:
        decision, stored = gcra(self._tat.get(key, 0), now, quota, mode)
        if stored is not None:
            if key not in self._tat and len(self._tat) >= MEMORY_MAX_KEYS:
                self._tat = {k: v for k, v in self._tat.items() if v > now}
            self._tat[key] = stored
        return decision

    async def reset(self, key: str) -> None:
        self._tat.pop(key, None)


class RedisGcraStore:
    """每次调用最多等待 `REDIS_TIMEOUT_S`，超时按 Redis 出错处理。"""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.script = redis.register_script(GCRA_SCRIPT)

    async def consume(self, key: str, quota: Quota, mode: Mode) -> Decision:
        async with asyncio.timeout(REDIS_TIMEOUT_S):
            raw = await self.script(keys=[key], args=[quota.emission_ms, quota.burst, mode.value])
        allowed, remaining, reset_ms, retry_ms = (int(value) for value in raw)
        return Decision(allowed == 1, quota.burst, remaining, reset_ms, retry_ms)

    async def reset(self, key: str) -> None:
        async with asyncio.timeout(REDIS_TIMEOUT_S):
            await self.redis.delete(key)
