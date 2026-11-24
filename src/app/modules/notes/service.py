"""notes 只对作者本人可见，其他人的笔记按不存在处理。"""

from typing import TYPE_CHECKING

from app.common.errors import AppError
from app.modules.notes.models import NoteModel
from app.modules.notes.repository import NoteRepository

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.pagination import SortField
    from app.modules.notes.models import NoteStatus
    from app.modules.notes.schemas import CreateNoteRequest, UpdateNoteRequest


class NotesService:
    def __init__(self, session: AsyncSession, owner_id: UUID) -> None:
        self.repository = NoteRepository(session)
        self.owner_id = str(owner_id)

    async def find_page(
        self, *, status: NoteStatus | None, sort: Sequence[SortField], offset: int, limit: int
    ) -> tuple[list[NoteModel], int]:
        return await self.repository.find_page(
            self.owner_id, status=status, sort=sort, offset=offset, limit=limit
        )

    async def get(self, note_id: UUID) -> NoteModel:
        note = await self.repository.find_owned(self.owner_id, note_id)
        if note is None:
            raise AppError("common.notFound")
        return note

    async def create(self, body: CreateNoteRequest) -> NoteModel:
        return await self.repository.add(
            NoteModel(
                owner_id=self.owner_id,
                title=body.title,
                content=body.content,
                status=body.status.value,
            )
        )

    async def update(self, note_id: UUID, body: UpdateNoteRequest) -> NoteModel:
        note = await self.get(note_id)
        if body.title is not None:
            note.title = body.title
        if body.content is not None:
            note.content = body.content
        if body.status is not None:
            note.status = body.status.value
        return await self.repository.save(note)

    async def delete(self, note_id: UUID) -> None:
        await self.repository.delete(await self.get(note_id))
