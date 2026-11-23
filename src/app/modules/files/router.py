"""约定 §11.5：`uploadFile`、`getFile`、`getFileContent`。"""

import re
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import Query, Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.requests import Request as StarletteRequest
from starlette.responses import FileResponse, Response

from app.common.context import Context
from app.common.errors import AppError, field_error
from app.common.middleware import PayloadTooLarge, limit_body
from app.common.routing import ApiRouter, Auth
from app.infrastructure.database.session import DbSession
from app.modules.files.purposes import FILE_WRITE, MAX_SIZE, Purpose, is_image
from app.modules.files.schemas import File
from app.modules.files.service import FilesService
from app.modules.files.storage import LocalStorage, content_disposition
from app.security.deps import CurrentPrincipal

MULTIPART_OVERHEAD_BYTES: Final = 64 * 1024
EXPIRES: Final = re.compile(r"[0-9]{1,12}")
UPLOAD_REQUEST_BODY: Final[dict[str, Any]] = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                    "purpose": {"type": "string", "enum": [purpose.value for purpose in Purpose]},
                },
                "required": ["file", "purpose"],
            }
        }
    },
}

router = ApiRouter(prefix="/v1/files", tags=["Files"])


async def _read_form(request: Request) -> tuple[UploadFile, str | None]:
    """以所有用途中最大的上限作为请求体上限；缺少 `file` 时 422 `required`。"""
    limited = StarletteRequest(
        request.scope,
        limit_body(request.scope, request.receive, MAX_SIZE + MULTIPART_OVERHEAD_BYTES),
    )
    try:
        form = await limited.form(max_files=1, max_fields=1)
    except PayloadTooLarge:
        raise AppError("file.tooLarge") from None
    except MultiPartException:
        raise AppError("common.badRequest") from None
    upload = form.get("file")
    purpose = form.get("purpose")
    if not isinstance(upload, UploadFile):
        raise field_error("file", "required")
    return upload, purpose if isinstance(purpose, str) else None


@router.endpoint(
    "POST",
    "",
    operation_id="uploadFile",
    auth=Auth.JWT,
    permissions=[FILE_WRITE],
    status=201,
    upload=True,
    request_body=UPLOAD_REQUEST_BODY,
    summary="上传文件",
)
async def upload_file(
    request: Request, principal: CurrentPrincipal, context: Context, session: DbSession
) -> File:
    upload, purpose = await _read_form(request)
    try:
        file = await FilesService(session).upload(
            context.settings.storage,
            owner_id=str(principal.sub),
            purpose_value=purpose,
            name=upload.filename or "",
            body=upload.file,
        )
    finally:
        await upload.close()
    return File.from_model(file, context.file_urls)


@router.endpoint("GET", "/{id}", operation_id="getFile", auth=Auth.JWT, summary="文件信息")
async def get_file(
    id: UUID, principal: CurrentPrincipal, context: Context, session: DbSession
) -> File:
    file = await FilesService(session).find_owned(id, str(principal.sub))
    return File.from_model(file, context.file_urls)


@router.endpoint(
    "GET",
    "/{id}/content",
    operation_id="getFileContent",
    auth=Auth.PUBLIC,
    envelope=False,
    summary="文件内容（签名地址）",
    description="`File.url` 指向本服务时使用；签名无效或过期 404。",
)
async def get_file_content(
    id: UUID,
    context: Context,
    session: DbSession,
    expires: Annotated[str | None, Query()] = None,
    signature: Annotated[str | None, Query()] = None,
) -> Response:
    urls = context.file_urls
    if (
        not isinstance(urls.storage, LocalStorage)
        or expires is None
        or not EXPIRES.fullmatch(expires)
        or signature is None
        or not urls.verify(id, int(expires), signature)
    ):
        raise AppError("common.notFound")
    file = await FilesService(session).find_by_id(id)
    if file is None:
        raise AppError("common.notFound")
    path = urls.storage.path(file.storage_key)
    if not path.is_file():
        raise AppError("common.notFound")
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    if not is_image(file.content_type):
        headers["Content-Disposition"] = content_disposition(file.name)
    return FileResponse(path, media_type=file.content_type, headers=headers)
