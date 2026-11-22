lazy from collections.abc import Sequence
lazy from datetime import datetime
lazy from uuid import UUID

from sqlalchemy import ColumnElement, or_, select, update
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.sessions.models import RefreshTokenModel, SessionModel


def _active(now: datetime) -> list[ColumnElement[bool]]:
    return [SessionModel.revoked_at.is_(None), SessionModel.expires_at > now]


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: UUID,
        user_agent: str | None,
        ip: str | None,
        auth_time: datetime,
        expires_at: datetime,
    ) -> SessionModel:
        record = SessionModel(
            user_id=user_id,
            user_agent=user_agent,
            ip=ip,
            auth_time=auth_time,
            last_active_at=auth_time,
            expires_at=expires_at,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def add_refresh_token(self, session_id: UUID, token_hash: str) -> RefreshTokenModel:
        row = RefreshTokenModel(session_id=session_id, token_hash=token_hash)
        self.session.add(row)
        await self.session.flush()
        return row

    async def find_refresh_token(self, token_hash: str) -> RefreshTokenModel | None:
        return await self.session.scalar(
            select(RefreshTokenModel).where(RefreshTokenModel.token_hash == token_hash)
        )

    async def lock(self, session_id: UUID) -> SessionModel | None:
        """`SELECT … FOR UPDATE` 锁住会话行，并重新读取其 refresh 记录。"""
        return await self.session.scalar(
            select(SessionModel)
            .where(SessionModel.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def reload_refresh_token(self, row: RefreshTokenModel) -> RefreshTokenModel:
        await self.session.refresh(row)
        return row

    async def find_active(
        self, session_id: UUID, user_id: UUID, now: datetime
    ) -> SessionModel | None:
        return await self.session.scalar(
            select(SessionModel).where(
                SessionModel.id == session_id, SessionModel.user_id == user_id, *_active(now)
            )
        )

    async def list_active(self, user_id: UUID, now: datetime) -> list[SessionModel]:
        return list(
            await self.session.scalars(
                select(SessionModel)
                .where(SessionModel.user_id == user_id, *_active(now))
                .order_by(SessionModel.last_active_at.desc().nulls_last(), SessionModel.id.desc())
            )
        )

    async def revoke(
        self, *, user_id: UUID, now: datetime, only: Sequence[UUID] = (), keep: UUID | None = None
    ) -> list[UUID]:
        """吊销该用户的有效会话（`only` 限定范围，`keep` 除外），返回被吊销的会话 id。"""
        conditions = [SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None)]
        if only:
            conditions.append(SessionModel.id.in_(only))
        if keep is not None:
            conditions.append(SessionModel.id != keep)
        result = await self.session.execute(
            update(SessionModel)
            .where(*conditions)
            .values(revoked_at=now)
            .returning(SessionModel.id)
        )
        return list(result.scalars())

    async def touch(self, session_id: UUID, now: datetime, stale_before: datetime) -> None:
        await self.session.execute(
            update(SessionModel)
            .where(
                SessionModel.id == session_id,
                or_(
                    SessionModel.last_active_at.is_(None),
                    SessionModel.last_active_at < stale_before,
                ),
            )
            .values(last_active_at=now)
        )
