"""与 nestjs cache-manager + Keyv 兼容的缓存：键 `cache::<key>`，值 `{"value", "expires"}`。"""

import json
import time
from typing import Any, Final, Protocol

lazy from redis.asyncio import Redis

NAMESPACE: Final = "cache"
DEFAULT_TTL_MS: Final = 60_000
MEMORY_MAX_ITEMS: Final = 10_000


class Cache(Protocol):
    async def get(self, key: str) -> Any: ...

    async def set(self, key: str, value: Any, ttl_ms: int = DEFAULT_TTL_MS) -> None: ...

    async def delete(self, key: str) -> None: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


class MemoryCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[Any, int]] = {}

    async def get(self, key: str) -> Any:
        item = self._items.get(key)
        if item is None:
            return None
        value, expires = item
        if expires <= _now_ms():
            self._items.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl_ms: int = DEFAULT_TTL_MS) -> None:
        now = _now_ms()
        self._items.pop(key, None)
        if len(self._items) >= MEMORY_MAX_ITEMS:
            self._items = {k: item for k, item in self._items.items() if item[1] > now}
            while len(self._items) >= MEMORY_MAX_ITEMS:
                del self._items[next(iter(self._items))]
        self._items[key] = (value, now + ttl_ms)

    async def delete(self, key: str) -> None:
        self._items.pop(key, None)


class RedisCache:
    def __init__(self, client: Redis) -> None:
        self.client = client

    @staticmethod
    def _key(key: str) -> str:
        return f"{NAMESPACE}::{key}"

    async def get(self, key: str) -> Any:
        raw = await self.client.get(self._key(key))
        if raw is None:
            return None
        payload = json.loads(raw)
        expires = payload.get("expires")
        if expires is not None and expires <= _now_ms():
            return None
        return payload.get("value")

    async def set(self, key: str, value: Any, ttl_ms: int = DEFAULT_TTL_MS) -> None:
        payload = json.dumps(
            {"value": value, "expires": _now_ms() + ttl_ms},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        await self.client.set(self._key(key), payload, px=ttl_ms)

    async def delete(self, key: str) -> None:
        await self.client.delete(self._key(key))
