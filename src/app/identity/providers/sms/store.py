"""短信验证码计数的原子操作；未配置 Redis 时用进程内实现。"""

import time
from typing import Final, Protocol

from redis.exceptions import RedisError
lazy from redis.asyncio import Redis

from app.common.errors import DependencyUnavailableError

INCR_WITH_TTL: Final = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
return n
"""

DECR_IF_EXISTS: Final = """
if redis.call('EXISTS', KEYS[1]) == 1 then return redis.call('DECR', KEYS[1]) end
return 0
"""

CHECK_CODE: Final = """
local stored = redis.call('GET', KEYS[1])
if not stored then return 0 end
if stored == ARGV[1] then
  redis.call('DEL', KEYS[1], KEYS[2])
  return 1
end
local n = redis.call('INCR', KEYS[2])
if n == 1 then redis.call('PEXPIRE', KEYS[2], math.max(redis.call('PTTL', KEYS[1]), 1)) end
if n >= tonumber(ARGV[2]) then redis.call('DEL', KEYS[1], KEYS[2]) end
return 0
"""


class SmsStoreUnavailableError(DependencyUnavailableError):
    pass


class SmsStore(Protocol):
    async def set_nx(self, key: str, ttl_ms: int) -> bool: ...

    async def set(self, key: str, value: str, ttl_ms: int) -> None: ...

    async def ttl_ms(self, key: str) -> int | None:
        """键不存在时为 None。"""
        ...

    async def increment(self, key: str, ttl_ms: int) -> int:
        """首次递增时设置 TTL。"""
        ...

    async def decrement(self, key: str) -> None:
        """键不存在时不操作。"""
        ...

    async def delete(self, *keys: str) -> None: ...

    async def check_code(
        self, code_key: str, attempts_key: str, digest: str, max_attempts: int
    ) -> bool:
        """匹配时删除验证码；不匹配时计次，达到 `max_attempts` 删除验证码。"""
        ...


class MemorySmsStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}

    def _get(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        if item[1] <= time.monotonic():
            del self._data[key]
            return None
        return item[0]

    def _put(self, key: str, value: str, ttl_ms: float) -> None:
        self._data[key] = (value, time.monotonic() + ttl_ms / 1000)

    async def set_nx(self, key: str, ttl_ms: int) -> bool:
        if self._get(key) is not None:
            return False
        self._put(key, "1", ttl_ms)
        return True

    async def set(self, key: str, value: str, ttl_ms: int) -> None:
        self._put(key, value, ttl_ms)

    async def ttl_ms(self, key: str) -> int | None:
        if self._get(key) is None:
            return None
        return max(1, int((self._data[key][1] - time.monotonic()) * 1000))

    def _increment(self, key: str, ttl_ms: float) -> int:
        current = self._get(key)
        if current is None:
            self._put(key, "1", ttl_ms)
            return 1
        value = int(current) + 1
        self._data[key] = (str(value), self._data[key][1])
        return value

    async def increment(self, key: str, ttl_ms: int) -> int:
        return self._increment(key, ttl_ms)

    async def decrement(self, key: str) -> None:
        current = self._get(key)
        if current is not None:
            self._data[key] = (str(int(current) - 1), self._data[key][1])

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._data.pop(key, None)

    async def check_code(
        self, code_key: str, attempts_key: str, digest: str, max_attempts: int
    ) -> bool:
        stored = self._get(code_key)
        if stored is None:
            return False
        if stored == digest:
            await self.delete(code_key, attempts_key)
            return True
        remaining = (self._data[code_key][1] - time.monotonic()) * 1000
        if self._increment(attempts_key, remaining) >= max_attempts:
            await self.delete(code_key, attempts_key)
        return False


class RedisSmsStore:
    """Redis 出错时抛 SmsStoreUnavailableError（503）。"""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self._increment = redis.register_script(INCR_WITH_TTL)
        self._check = redis.register_script(CHECK_CODE)
        self._decrement = redis.register_script(DECR_IF_EXISTS)

    async def set_nx(self, key: str, ttl_ms: int) -> bool:
        try:
            return bool(await self.redis.set(key, "1", nx=True, px=ttl_ms))
        except RedisError as error:
            raise SmsStoreUnavailableError(str(error)) from error

    async def set(self, key: str, value: str, ttl_ms: int) -> None:
        try:
            await self.redis.set(key, value, px=ttl_ms)
        except RedisError as error:
            raise SmsStoreUnavailableError(str(error)) from error

    async def ttl_ms(self, key: str) -> int | None:
        try:
            ttl = int(await self.redis.pttl(key))
        except RedisError as error:
            raise SmsStoreUnavailableError(str(error)) from error
        return ttl if ttl > 0 else None

    async def increment(self, key: str, ttl_ms: int) -> int:
        try:
            return int(await self._increment(keys=[key], args=[ttl_ms]))
        except RedisError as error:
            raise SmsStoreUnavailableError(str(error)) from error

    async def decrement(self, key: str) -> None:
        try:
            await self._decrement(keys=[key])
        except RedisError as error:
            raise SmsStoreUnavailableError(str(error)) from error

    async def delete(self, *keys: str) -> None:
        try:
            await self.redis.delete(*keys)
        except RedisError as error:
            raise SmsStoreUnavailableError(str(error)) from error

    async def check_code(
        self, code_key: str, attempts_key: str, digest: str, max_attempts: int
    ) -> bool:
        try:
            return bool(
                await self._check(keys=[code_key, attempts_key], args=[digest, max_attempts])
            )
        except RedisError as error:
            raise SmsStoreUnavailableError(str(error)) from error


def sms_store(redis: Redis | None) -> SmsStore:
    return RedisSmsStore(redis) if redis is not None else MemorySmsStore()
