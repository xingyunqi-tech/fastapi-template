"""files 路由：按 FILE_DRIVER 注册上传接口（local / s3 / s3-presigned），以及私有文件读取。"""

import mimetypes
from pathlib import Path as FsPath
from typing import TYPE_CHECKING, Annotated, Any, Final
from uuid import UUID

from fastapi import Depends, Path, Request
from starlette.responses import Response

from app.common.context import AppContext, Context
from app.common.errors import AppError
from app.common.routing import ApiRouter, Auth
from app.config import FileDriver
from app.infrastructure.database.session import DbSession
from app.modules.files.purposes import (
    FILE_PURPOSE_RULES,
    FilePurpose,
    FileVisibility,
    file_owner_for,
    file_too_large_error,
    file_type_not_allowed_error,
    invalid_purpose_error,
    mime_type_for,
    parse_file_purpose,
    select_file_error,
)
from app.modules.files.schemas import (
    FileInfo,
    PresignedUpload,
    PresignedUploadRequest,
    UploadedFile,
)
from app.modules.files.send import file_response
from app.modules.files.service import FilesService
from app.modules.files.storage import (
    API_PREFIX,
    LOCAL_FILES_ROOT,
    FileStorage,
    local_disk_path,
    local_file_url_path,
    presigned_put_url,
    random_key,
)
from app.modules.files.uploads import (
    FileSink,
    LocalSink,
    ReceivedFile,
    S3Sink,
    receive_single_file,
)
from app.security.deps import CurrentPrincipal
from app.security.rbac import permissions_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.modules.files.models import FileModel

MULTIPART_OVERHEAD_BYTES: Final = 16 * 1024
MULTIPART_REQUEST_BODY: Final = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {"file": {"type": "string", "format": "binary"}},
                "required": ["file"],
            }
        }
    },
}
PURPOSE_PARAMETER: Final = {
    "name": "purpose",
    "in": "query",
    "required": False,
    "schema": {"type": "string", "enum": [purpose.value for purpose in FilePurpose]},
}
_CONTENT_TYPES: Final = {
    extension: mime
    for rule in FILE_PURPOSE_RULES.values()
    for extension, mime in rule.mime_types.items()
}

files_router = ApiRouter(prefix="/v1/files", tags=["Files"])
local_router = ApiRouter(prefix="/v1/files", tags=["Files"])
s3_router = ApiRouter(prefix="/v1/files", tags=["Files"])
s3_presigned_router = ApiRouter(prefix="/v1/files", tags=["Files"])


def routers_for(driver: FileDriver) -> list[ApiRouter]:
    """先注册驱动对应的上传接口，再注册私有文件读取。"""
    uploader = {
        FileDriver.LOCAL: local_router,
        FileDriver.S3: s3_router,
        FileDriver.S3_PRESIGNED: s3_presigned_router,
    }[driver]
    return [uploader, files_router]


def _content_length(request: Request) -> float | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return float(raw.strip() or 0)
    except ValueError:
        return None


async def upload_purpose_guard(
    request: Request, principal: CurrentPrincipal, context: Context
) -> FilePurpose:
    """用途合法、具备用途所需权限、Content-Length 未明显超限。"""
    values = request.query_params.getlist("purpose")
    purpose = parse_file_purpose(values[0]) if len(values) == 1 and values[0] else None
    if purpose is None and not values:
        purpose = FilePurpose.IMAGE
    if purpose is None:
        raise invalid_purpose_error()
    permission = FILE_PURPOSE_RULES[purpose].permission
    if permission is not None and permission not in permissions_for(principal.roles):
        raise AppError("common.forbidden")
    length = _content_length(request)
    max_size = context.settings.file.max_sizes[purpose]
    if length is not None and length > max_size + MULTIPART_OVERHEAD_BYTES:
        raise file_too_large_error()
    return purpose


UploadPurpose = Annotated[FilePurpose, Depends(upload_purpose_guard)]


def _sink_factory(storage: FileStorage, purpose: FilePurpose) -> Any:
    def open_sink(file_name: str) -> FileSink:
        key = random_key(file_name)
        if storage.is_local:
            return LocalSink(key)
        content_type = mime_type_for(purpose, file_name) or "application/octet-stream"
        return S3Sink(storage, key, content_type)

    return open_sink


async def received_file(
    request: Request, purpose: UploadPurpose, context: Context
) -> AsyncIterator[ReceivedFile | None]:
    """接收 `file` 字段；后续校验或处理失败时删除已存储的文件。"""
    settings = context.settings.file

    def file_filter(file_name: str) -> None:
        if mime_type_for(purpose, file_name) is None:
            raise file_type_not_allowed_error()

    received = await receive_single_file(
        request,
        file_filter=file_filter,
        open_sink=_sink_factory(FileStorage(settings), purpose),
        max_file_size=settings.max_file_size,
    )
    try:
        yield received
    except BaseException:
        if received is not None:
            await received.sink.discard()
        raise


ReceivedUpload = Annotated[ReceivedFile | None, Depends(received_file, scope="function")]


async def _store_received(
    received: ReceivedFile | None,
    purpose: FilePurpose,
    user_id: UUID,
    context: AppContext,
    session: DbSession,
) -> UploadedFile:
    if received is None:
        raise select_file_error()
    settings = context.settings
    if received.size > settings.file.max_sizes[purpose]:
        raise file_too_large_error()
    key = received.sink.key
    path = local_file_url_path(API_PREFIX, key) if settings.file.driver is FileDriver.LOCAL else key
    file = await FilesService(session).create(
        path=path,
        owner_user_id=file_owner_for(purpose, user_id),
        visibility=FILE_PURPOSE_RULES[purpose].visibility,
        mime_type=mime_type_for(purpose, received.original_name),
        size=received.size,
    )
    return UploadedFile(file=FileInfo.from_model(file, context.file_urls))


@local_router.endpoint(
    "POST",
    "/upload",
    operation_id="uploadFile",
    auth=Auth.JWT,
    status=201,
    upload=True,
    request_body=MULTIPART_REQUEST_BODY,
    parameters=[PURPOSE_PARAMETER],
    summary="上传文件",
)
async def upload_local_file(
    purpose: UploadPurpose,
    received: ReceivedUpload,
    principal: CurrentPrincipal,
    context: Context,
    session: DbSession,
) -> UploadedFile:
    return await _store_received(received, purpose, principal.sub, context, session)


@s3_router.endpoint(
    "POST",
    "/upload",
    operation_id="uploadFile",
    auth=Auth.JWT,
    status=201,
    upload=True,
    request_body=MULTIPART_REQUEST_BODY,
    parameters=[PURPOSE_PARAMETER],
    summary="上传文件",
)
async def upload_s3_file(
    purpose: UploadPurpose,
    received: ReceivedUpload,
    principal: CurrentPrincipal,
    context: Context,
    session: DbSession,
) -> UploadedFile:
    return await _store_received(received, purpose, principal.sub, context, session)


@s3_presigned_router.endpoint(
    "POST",
    "/upload",
    operation_id="uploadFile",
    auth=Auth.JWT,
    status=201,
    parameters=[PURPOSE_PARAMETER],
    summary="上传文件",
)
async def upload_presigned_file(
    purpose: UploadPurpose,
    body: PresignedUploadRequest,
    principal: CurrentPrincipal,
    context: Context,
    session: DbSession,
) -> PresignedUpload:
    mime_type = mime_type_for(purpose, body.file_name)
    if mime_type is None:
        raise file_type_not_allowed_error()
    if body.file_size > context.settings.file.max_sizes[purpose]:
        raise file_too_large_error()
    storage = FileStorage(context.settings.file)
    key = random_key(body.file_name)
    signed_url = presigned_put_url(storage.s3_options(), key, body.file_size)
    file = await FilesService(session).create(
        path=key,
        owner_user_id=file_owner_for(purpose, principal.sub),
        visibility=FILE_PURPOSE_RULES[purpose].visibility,
        mime_type=mime_type,
        size=body.file_size,
    )
    return PresignedUpload(
        file=FileInfo.from_model(file, context.file_urls), upload_signed_url=signed_url
    )


def _content_type(path: FsPath) -> str:
    extension = path.suffix.removeprefix(".").lower()
    return (
        _CONTENT_TYPES.get(extension)
        or mimetypes.guess_type(path.name)[0]
        or "application/octet-stream"
    )


def send_file(request: Request, path: FsPath) -> Response:
    try:
        stat = path.stat()
    except OSError:
        raise AppError("common.notFound") from None
    return file_response(request.headers, path, stat, _content_type(path))


def redirect(request: Request, url: str) -> Response:
    """302，正文随 Accept 选择 text / html。"""
    accept = request.headers.get("accept") or ""
    if "text/html" in accept and "text/plain" not in accept:
        escaped = (
            url.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )
        body = (
            "<!DOCTYPE html><head><title>Found</title></head>"
            f"<body><p>Found. Redirecting to {escaped}</p></body>"
        )
        media_type = "text/html; charset=utf-8"
    else:
        body = f"Found. Redirecting to {url}"
        media_type = "text/plain; charset=utf-8"
    return Response(
        body, status_code=302, media_type=media_type, headers={"Location": url, "Vary": "Accept"}
    )


@files_router.endpoint(
    "GET",
    "/private/{id}",
    operation_id="readPrivateFile",
    auth=Auth.JWT,
    include_in_schema=False,
)
async def read_private_file(
    request: Request,
    id: Annotated[UUID, Path()],
    principal: CurrentPrincipal,
    context: Context,
    session: DbSession,
) -> Response:
    file = await FilesService(session).find_accessible(id, principal.sub)
    if context.settings.file.driver is FileDriver.LOCAL:
        return send_file(request, local_disk_path(file.path))
    return redirect(request, context.file_urls.build(file))


async def _find_public_by_file_name(
    session: DbSession, api_prefix: str, file_name: str
) -> FileModel | None:
    file = await FilesService(session).find_by_path(local_file_url_path(api_prefix, file_name))
    return file if file is not None and file.visibility == FileVisibility.PUBLIC else None


@local_router.endpoint(
    "GET",
    "/{path}",
    operation_id="downloadFile",
    auth=Auth.PUBLIC,
    include_in_schema=False,
)
async def download_file(
    request: Request, path: Annotated[str, Path()], context: Context, session: DbSession
) -> Response:
    name = FsPath(path).name
    file = await _find_public_by_file_name(session, API_PREFIX, name)
    if file is None or name != path:
        raise AppError("common.notFound")
    return send_file(request, LOCAL_FILES_ROOT / name)
