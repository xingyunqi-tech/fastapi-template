"""会话：创建、刷新轮换（CAS + 60s 宽限）、`lastActiveAt` 节流、吊销与鉴权缓存失效。"""

from datetime import UTC, datetime, timedelta
from typing import Final
lazy from collections.abc import Iterable

from app.infrastructure.database.session import after_commit
lazy from app.core.session.models import SessionModel
lazy from app.core.session.repository import SessionRepository
lazy from app.infrastructure.cache import Cache

ROTATED_HASH_GRACE_MS: Final = 60_000
LAST_ACTIVE_MIN_INTERVAL: Final = timedelta(minutes=5)


def session_auth_cache_key(session_id: int) -> str:
    return f"jwt:session:{session_id}"


def _rotated_hash_cache_key(session_id: int) -> str:
    return f"session:rotated:{session_id}"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SessionService:
    def __init__(self, repository: SessionRepository, cache: Cache) -> None:
        self.repository = repository
        self.cache = cache

    async def find_by_id(self, session_id: int) -> SessionModel | None:
        return await self.repository.find_by_id(session_id)

    async def create(
        self, *, user_id: int, hash_: str, user_agent: str | None, ip: str | None
    ) -> SessionModel:
        return await self.repository.create(
            user_id=user_id, hash_=hash_, user_agent=user_agent, ip=ip, last_active_at=utcnow()
        )

    async def find_active_by_user(self, user_id: int) -> list[SessionModel]:
        return await self.repository.find_active_by_user(user_id)

    async def touch(self, session_id: int) -> None:
        now = utcnow()
        await self.repository.touch(session_id, now, now - LAST_ACTIVE_MIN_INTERVAL)

    async def rotate_hash(self, session_id: int, current: str, next_: str) -> bool:
        rotated = await self.repository.rotate_hash(session_id, current, next_)
        if rotated:
            await self.cache.set(
                _rotated_hash_cache_key(session_id), current, ROTATED_HASH_GRACE_MS
            )
        return rotated

    async def was_recently_rotated_from(self, session_id: int, hash_: str) -> bool:
        return bool(await self.cache.get(_rotated_hash_cache_key(session_id)) == hash_)

    async def forget(self, session_ids: Iterable[int]) -> None:
        """立即清除鉴权缓存，并在事务提交后再清一次，避免并发请求在提交前回填旧值。"""
        keys = [session_auth_cache_key(session_id) for session_id in session_ids]

        async def clear() -> None:
            for key in keys:
                await self.cache.delete(key)

        await clear()
        after_commit(self.repository.session, clear)

    async def revoke_for_user(self, session_id: int, user_id: int) -> bool:
        revoked = await self.repository.soft_delete_for_user(session_id, user_id)
        if revoked:
            await self.forget([session_id])
        return revoked

    async def delete(self, session_id: int) -> None:
        await self.repository.soft_delete(session_id)
        await self.forget([session_id])

    async def delete_by_user(self, user_id: int, *, exclude: int | None = None) -> None:
        ids = await self.repository.soft_delete_by_user(user_id, exclude)
        await self.forget(ids)
