"""notes CRUD 示例：本人的笔记，权限 `note:read` / `note:write`。"""

from typing import Annotated, Final
from uuid import UUID

from fastapi import Depends, Path, Query

from app.common.pagination import PageParams, SortField, sort_param
from app.common.routing import ApiRouter, Auth
from app.infrastructure.database.session import DbSession
from app.modules.notes.models import NoteStatus
from app.modules.notes.repository import SORTABLE
from app.modules.notes.schemas import CreateNoteRequest, Note, NotePage, UpdateNoteRequest
from app.modules.notes.service import NotesService
from app.security import rbac
from app.security.deps import CurrentPrincipal
from app.security.rbac import Role

NOTE_READ: Final = rbac.register("note:read", roles=[Role.USER])
NOTE_WRITE: Final = rbac.register("note:write", roles=[Role.USER])

router = ApiRouter(prefix="/v1/notes", tags=["Notes"])

NoteId = Annotated[UUID, Path()]
NoteSort = Annotated[list[SortField], Depends(sort_param(tuple(SORTABLE), "-createdAt"))]


@router.endpoint(
    "GET", "", operation_id="listNotes", auth=Auth.JWT, permissions=[NOTE_READ], summary="笔记列表"
)
async def list_notes(
    page: Annotated[PageParams, Query()],
    sort: NoteSort,
    principal: CurrentPrincipal,
    session: DbSession,
    status: Annotated[NoteStatus | None, Query()] = None,
) -> NotePage:
    notes, total = await NotesService(session, principal.sub).find_page(
        status=status, sort=sort, offset=page.offset, limit=page.page_size
    )
    return NotePage.of([Note.model_validate(note) for note in notes], total, page)


@router.endpoint(
    "POST",
    "",
    operation_id="createNote",
    auth=Auth.JWT,
    status=201,
    permissions=[NOTE_WRITE],
    summary="创建笔记",
)
async def create_note(
    body: CreateNoteRequest, principal: CurrentPrincipal, session: DbSession
) -> Note:
    return Note.model_validate(await NotesService(session, principal.sub).create(body))


@router.endpoint(
    "GET", "/{id}", operation_id="getNote", auth=Auth.JWT, permissions=[NOTE_READ], summary="笔记"
)
async def get_note(id: NoteId, principal: CurrentPrincipal, session: DbSession) -> Note:
    return Note.model_validate(await NotesService(session, principal.sub).get(id))


@router.endpoint(
    "PATCH",
    "/{id}",
    operation_id="updateNote",
    auth=Auth.JWT,
    permissions=[NOTE_WRITE],
    summary="修改笔记",
)
async def update_note(
    id: NoteId, body: UpdateNoteRequest, principal: CurrentPrincipal, session: DbSession
) -> Note:
    return Note.model_validate(await NotesService(session, principal.sub).update(id, body))


@router.endpoint(
    "DELETE",
    "/{id}",
    operation_id="deleteNote",
    auth=Auth.JWT,
    permissions=[NOTE_WRITE],
    summary="删除笔记",
)
async def delete_note(id: NoteId, principal: CurrentPrincipal, session: DbSession) -> None:
    await NotesService(session, principal.sub).delete(id)
