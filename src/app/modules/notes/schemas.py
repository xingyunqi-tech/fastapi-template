"""notes 的请求与响应模型。"""

from typing import Annotated
from uuid import UUID

from pydantic import StringConstraints

from app.common.pagination import Page
from app.common.types import ApiModel, UtcDatetime
from app.modules.notes.models import NoteStatus

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Content = Annotated[str, StringConstraints(max_length=10_000)]


class Note(ApiModel):
    id: UUID
    title: str
    content: str
    status: NoteStatus
    created_at: UtcDatetime
    updated_at: UtcDatetime


class NotePage(Page[Note]):
    pass


class CreateNoteRequest(ApiModel):
    title: Title
    content: Content = ""
    status: NoteStatus = NoteStatus.DRAFT


class UpdateNoteRequest(ApiModel):
    title: Title | None = None
    content: Content | None = None
    status: NoteStatus | None = None
