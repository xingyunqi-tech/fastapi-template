from typing import TYPE_CHECKING, Final

from sqlalchemy import delete, func, select

from app.modules.notes.models import NoteModel

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.pagination import SortField
    from app.modules.notes.models import NoteStatus

SORTABLE: Final = {
    "createdAt": NoteModel.created_at,
    "updatedAt": NoteModel.updated_at,
    "title": NoteModel.title,
}


class NoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_page(
        self,
        owner_id: str,
        *,
        status: NoteStatus | None,
        sort: Sequence[SortField],
        offset: int,
        limit: int,
    ) -> tuple[list[NoteModel], int]:
        conditions = [NoteModel.owner_id == owner_id]
        if status is not None:
            conditions.append(NoteModel.status == status.value)
        order = [
            SORTABLE[field.field].desc() if field.descending else SORTABLE[field.field].asc()
            for field in sort
        ]
        rows = await self.session.scalars(
            select(NoteModel)
            .where(*conditions)
            .order_by(*order, NoteModel.id)
            .offset(offset)
            .limit(limit)
        )
        total = await self.session.scalar(
            select(func.count()).select_from(NoteModel).where(*conditions)
        )
        return list(rows), int(total or 0)

    async def find_owned(self, owner_id: str, note_id: UUID) -> NoteModel | None:
        return await self.session.scalar(
            select(NoteModel).where(NoteModel.id == note_id, NoteModel.owner_id == owner_id)
        )

    async def add(self, note: NoteModel) -> NoteModel:
        self.session.add(note)
        await self.session.flush()
        await self.session.refresh(note)
        return note

    async def save(self, note: NoteModel) -> NoteModel:
        await self.session.flush()
        await self.session.refresh(note)
        return note

    async def delete(self, note: NoteModel) -> None:
        await self.session.delete(note)
        await self.session.flush()

    async def delete_by_owner(self, owner_id: str) -> None:
        await self.session.execute(delete(NoteModel).where(NoteModel.owner_id == owner_id))
