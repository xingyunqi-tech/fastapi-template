lazy from datetime import datetime

from sqlalchemy import ColumnElement, func, or_, select, update
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.core.session.models import SessionModel
from app.infrastructure.database.session import affected_rows


def _active() -> ColumnElement[bool]:
    return SessionModel.deleted_at.is_(None)


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_by_id(self, session_id: int) -> SessionModel | None:
        return await self.session.scalar(
            select(SessionModel).where(SessionModel.id == session_id, _active())
        )

    async def create(
        self,
        *,
        user_id: int,
        hash_: str,
        user_agent: str | None,
        ip: str | None,
        last_active_at: datetime,
    ) -> SessionModel:
        model = SessionModel(
            user_id=user_id, hash=hash_, user_agent=user_agent, ip=ip, last_active_at=last_active_at
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def rotate_hash(self, session_id: int, current: str, next_: str) -> bool:
        result = await self.session.execute(
            update(SessionModel)
            .where(SessionModel.id == session_id, SessionModel.hash == current, _active())
            .values(hash=next_, last_active_at=func.now(), updated_at=func.now())
        )
        return affected_rows(result) > 0

    async def find_active_by_user(self, user_id: int) -> list[SessionModel]:
        statement = (
            select(SessionModel)
            .where(SessionModel.user_id == user_id, _active())
            .order_by(SessionModel.last_active_at.desc().nulls_last(), SessionModel.id.desc())
        )
        return list(await self.session.scalars(statement))

    async def touch(self, session_id: int, at: datetime, stale_before: datetime) -> None:
        await self.session.execute(
            update(SessionModel)
            .where(
                SessionModel.id == session_id,
                or_(
                    SessionModel.last_active_at.is_(None),
                    SessionModel.last_active_at < stale_before,
                ),
            )
            .values(last_active_at=at, updated_at=func.now())
        )

    async def soft_delete_for_user(self, session_id: int, user_id: int) -> bool:
        result = await self.session.execute(
            update(SessionModel)
            .where(SessionModel.id == session_id, SessionModel.user_id == user_id, _active())
            .values(deleted_at=func.now(), updated_at=func.now())
        )
        return affected_rows(result) > 0

    async def soft_delete(self, session_id: int) -> None:
        await self.session.execute(
            update(SessionModel)
            .where(SessionModel.id == session_id, _active())
            .values(deleted_at=func.now(), updated_at=func.now())
        )

    async def soft_delete_by_user(self, user_id: int, exclude: int | None = None) -> list[int]:
        conditions: list[ColumnElement[bool]] = [SessionModel.user_id == user_id, _active()]
        if exclude is not None:
            conditions.append(SessionModel.id != exclude)
        result = await self.session.execute(
            update(SessionModel)
            .where(*conditions)
            .values(deleted_at=func.now(), updated_at=func.now())
            .returning(SessionModel.id)
        )
        return list(result.scalars())
