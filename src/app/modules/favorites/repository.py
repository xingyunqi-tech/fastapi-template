from typing import Any
lazy from uuid import UUID

from sqlalchemy import ColumnElement, delete, func, select
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import offset_limit
from app.modules.favorites.models import FavoriteModel


def _where(
    user_id: int, target_type: str | None, subject_id: UUID | None
) -> list[ColumnElement[bool]]:
    conditions = [FavoriteModel.user_id == user_id]
    if target_type:
        conditions.append(FavoriteModel.target_type == target_type)
    if subject_id:
        conditions.append(FavoriteModel.subject_id == subject_id)
    return conditions


class FavoriteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, values: dict[str, Any]) -> FavoriteModel:
        favorite = FavoriteModel(**values)
        self.session.add(favorite)
        await self.session.flush()
        await self.session.refresh(favorite)
        return favorite

    async def find_many(
        self,
        user_id: int,
        *,
        target_type: str | None,
        subject_id: UUID | None,
        page: float,
        limit: float,
    ) -> list[FavoriteModel]:
        offset, size = offset_limit(page, limit)
        statement = (
            select(FavoriteModel)
            .where(*_where(user_id, target_type, subject_id))
            .order_by(FavoriteModel.created_at.desc())
            .offset(offset)
            .limit(size)
        )
        return list(await self.session.scalars(statement))

    async def count(self, user_id: int, *, target_type: str | None, subject_id: UUID | None) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(FavoriteModel)
            .where(*_where(user_id, target_type, subject_id))
        )
        return int(total or 0)

    async def find_by_id(self, favorite_id: int) -> FavoriteModel | None:
        return await self.session.scalar(
            select(FavoriteModel).where(FavoriteModel.id == favorite_id)
        )

    async def find_by_user_and_target(
        self, user_id: int, target_type: str, target_id: str
    ) -> FavoriteModel | None:
        return await self.session.scalar(
            select(FavoriteModel)
            .where(
                FavoriteModel.user_id == user_id,
                FavoriteModel.target_type == target_type,
                FavoriteModel.target_id == target_id,
            )
            .limit(1)
        )

    async def remove(self, favorite_id: int) -> None:
        await self.session.execute(delete(FavoriteModel).where(FavoriteModel.id == favorite_id))

    async def remove_by_user(self, user_id: int) -> None:
        await self.session.execute(delete(FavoriteModel).where(FavoriteModel.user_id == user_id))
