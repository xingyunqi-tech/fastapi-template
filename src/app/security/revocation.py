"""约定 §5.1 会话吊销键 `auth:revoked:sid:{sid}`；未配置 Redis 时只在本进程内生效。"""

import time
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from redis.exceptions import RedisError

from app.common.errors import DependencyUnavailableError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID

    from redis.asyncio import Redis

KEY_PREFIX: Final = "auth:revoked:sid:"
EXTRA_TTL: Final = timedelta(seconds=60)


def revocation_key(sid: UUID) -> str:
    return f"{KEY_PREFIX}{sid}"


class RevocationUnavailableError(DependencyUnavailableError):
    pass


class Revocations:
    def __init__(self, redis: Redis | None, access_ttl: timedelta) -> None:
        self.redis = redis
        self.ttl_ms = int((access_ttl + EXTRA_TTL).total_seconds() * 1000)
        self._local: dict[UUID, float] = {}

    async def revoke(self, sids: Iterable[UUID]) -> None:
        """Redis 出错时抛 RevocationUnavailableError（503），调用方的事务回滚。"""
        unique = list(dict.fromkeys(sids))
        if not unique:
            return
        if self.redis is None:
            expires = time.monotonic() + self.ttl_ms / 1000
            self._local.update(dict.fromkeys(unique, expires))
            return
        try:
            async with self.redis.pipeline(transaction=False) as pipe:
                for sid in unique:
                    pipe.set(revocation_key(sid), "1", px=self.ttl_ms)
                await pipe.execute()
        except RedisError as error:
            raise RevocationUnavailableError(str(error)) from error

    async def is_revoked(self, sid: UUID) -> bool:
        """Redis 出错时抛 RevocationUnavailableError（503）。"""
        if self.redis is None:
            expires = self._local.get(sid)
            if expires is not None and expires <= time.monotonic():
                del self._local[sid]
                return False
            return expires is not None
        try:
            return bool(await self.redis.exists(revocation_key(sid)))
        except RedisError as error:
            raise RevocationUnavailableError(str(error)) from error
