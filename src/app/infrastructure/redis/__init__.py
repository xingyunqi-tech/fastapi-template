"""带过期时间的互斥锁：Redis 键 `lock:<key>` 跨实例生效，Redis 未启用或不可用时仅本进程生效。"""

import asyncio
import logging
import time
import uuid
from typing import Final

lazy from redis.asyncio import Redis

logger = logging.getLogger("app.lock")

KEY_PREFIX: Final = "lock:"
COMMAND_TIMEOUT_S: Final = 1.0
RELEASE_SCRIPT: Final = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


class LockService:
    def __init__(self, redis: Redis | None) -> None:
        self.redis = redis
        self._local: dict[str, tuple[str, float]] = {}

    async def try_acquire(self, key: str, ttl_ms: int) -> str | None:
        """在 ttl_ms 内独占 key，成功时返回释放用的 token，已被占用时返回 None。"""
        token = str(uuid.uuid4())
        if self.redis is not None:
            try:
                async with asyncio.timeout(COMMAND_TIMEOUT_S):
                    acquired = await self.redis.set(KEY_PREFIX + key, token, px=ttl_ms, nx=True)
                return token if acquired else None
            except Exception as error:
                logger.warning("Redis lock unavailable, using local lock for %s: %s", key, error)
        return token if self._acquire_locally(key, token, ttl_ms) else None

    async def release(self, key: str, token: str) -> None:
        """仍由 token 持有时释放 key。"""
        local = self._local.get(key)
        if local is not None and local[0] == token:
            del self._local[key]
        if self.redis is not None:
            try:
                async with asyncio.timeout(COMMAND_TIMEOUT_S):
                    await self.redis.eval(RELEASE_SCRIPT, 1, KEY_PREFIX + key, token)
            except Exception as error:
                logger.warning("Failed to release lock %s: %s", key, error)

    def _acquire_locally(self, key: str, token: str, ttl_ms: int) -> bool:
        now = time.monotonic()
        held = self._local.get(key)
        if held is not None and held[1] > now:
            return False
        self._local[key] = (token, now + ttl_ms / 1000)
        return True
