"""files 路由：按 FILE_DRIVER 注册上传接口（local / s3 / s3-presigned），以及私有文件读取。"""

import mimetypes
import re
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path as FsPath
from typing import TYPE_CHECKING, Annotated, Any, Final
from uuid import UUID

from fastapi import Depends, Path, Request
from starlette.responses import FileResponse, Response

from app.common.context import AppContext, Context
from app.common.exceptions import bad_request, forbidden, not_found
from app.common.params import Body, Query, parse_query_string
from app.common.routing import ApiRoute, ApiRouter, Auth
from app.config import FileDriver
from app.core.auth.deps import CurrentUser
from app.core.permissions.service import PermissionsService
from app.core.users.serializers import serialize_file
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
    FileResponseDto,
    FileUploadDto,
    PresignedFileResponseDto,
    UploadFileQueryDto,
)
from app.modules.files.service import FilesService
from app.modules.files.storage import (
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

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.datastructures import Headers

    from app.modules.files.models import FileModel

MULTIPART_OVERHEAD_BYTES: Final = 16 * 1024
MULTIPART_REQUEST_BODY: Final = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "properties": {"file": {"type": "string", "format": "binary"}},
            }
        }
    },
}
_UUID_RE: Final = re.compile(
    r"(?:[0-9A-F]{8}-[0-9A-F]{4}-[1-8][0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}"
    r"|00000000-0000-0000-0000-000000000000|FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF)",
    re.IGNORECASE,
)
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
    """与 nestjs FilesModule 一致：先注册驱动对应的上传 controller，再注册私有文件读取。"""
    uploader = {
        FileDriver.LOCAL: local_router,
        FileDriver.S3: s3_router,
        FileDriver.S3_PRESIGNED: s3_presigned_router,
    }[driver]
    return [uploader, files_router]


def _with_request_body(router: ApiRouter, body: dict[str, Any]) -> None:
    route = router.routes[-1]
    if not isinstance(route, ApiRoute):
        raise TypeError(f"{route} 不是 ApiRoute")
    route.openapi_extra = {**(route.openapi_extra or {}), "requestBody": body}


def _content_length(request: Request) -> float | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        return float(raw.strip() or 0)
    except ValueError:
        return None


async def upload_purpose_guard(
    request: Request, user: CurrentUser, context: Context, session: DbSession
) -> FilePurpose:
    """nestjs FileUploadPurposeGuard：用途合法、具备用途所需权限、Content-Length 未明显超限。"""
    query = parse_query_string(request.scope.get("query_string", b"").decode("latin-1"))
    purpose = parse_file_purpose(query.get("purpose"))
    if purpose is None:
        raise invalid_purpose_error()
    permission = FILE_PURPOSE_RULES[purpose].permission
    if permission is not None:
        granted = (
            await PermissionsService(session, context.cache).permissions_for_role(user.role_id)
            if user.role_id is not None
            else []
        )
        if permission not in granted:
            raise forbidden(f"Missing permissions: {permission}")
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
    """multer 接收 `file` 字段；后续校验或处理失败时删除已存储的文件。"""
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
PurposeQuery = Annotated[UploadFileQueryDto, Query(UploadFileQueryDto)]


async def _store_received(
    received: ReceivedFile | None,
    purpose: FilePurpose,
    user_id: int,
    context: AppContext,
    session: DbSession,
) -> dict[str, Any]:
    if received is None:
        raise select_file_error()
    settings = context.settings
    if received.size > settings.file.max_sizes[purpose]:
        raise file_too_large_error()
    key = received.sink.key
    path = (
        local_file_url_path(settings.app.api_prefix, key)
        if settings.file.driver is FileDriver.LOCAL
        else key
    )
    file = await FilesService(session).create(
        path=path,
        owner_user_id=file_owner_for(purpose, user_id),
        visibility=FILE_PURPOSE_RULES[purpose].visibility,
        mime_type=mime_type_for(purpose, received.original_name),
        size=received.size,
    )
    return {"file": serialize_file(file, context.file_urls)}


@local_router.endpoint(
    "POST",
    "/upload",
    operation_id="uploadFile",
    auth=Auth.JWT,
    status_code=201,
    response_model=FileResponseDto,
    summary="上传文件",
)
async def upload_local_file(
    purpose: UploadPurpose,
    received: ReceivedUpload,
    query: PurposeQuery,
    user: CurrentUser,
    context: Context,
    session: DbSession,
) -> dict[str, Any]:
    return await _store_received(received, purpose, user.id, context, session)


_with_request_body(local_router, MULTIPART_REQUEST_BODY)


@s3_router.endpoint(
    "POST",
    "/upload",
    operation_id="uploadFile",
    auth=Auth.JWT,
    status_code=201,
    response_model=FileResponseDto,
    summary="上传文件",
)
async def upload_s3_file(
    purpose: UploadPurpose,
    received: ReceivedUpload,
    query: PurposeQuery,
    user: CurrentUser,
    context: Context,
    session: DbSession,
) -> dict[str, Any]:
    return await _store_received(received, purpose, user.id, context, session)


_with_request_body(s3_router, MULTIPART_REQUEST_BODY)


@s3_presigned_router.endpoint(
    "POST",
    "/upload",
    operation_id="uploadFile",
    auth=Auth.JWT,
    status_code=201,
    response_model=PresignedFileResponseDto,
    summary="上传文件",
)
async def upload_presigned_file(
    purpose: UploadPurpose,
    body: Annotated[FileUploadDto, Body(FileUploadDto)],
    query: PurposeQuery,
    user: CurrentUser,
    context: Context,
    session: DbSession,
) -> dict[str, Any]:
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
        owner_user_id=file_owner_for(purpose, user.id),
        visibility=FILE_PURPOSE_RULES[purpose].visibility,
        mime_type=mime_type,
        size=int(body.file_size),
    )
    return {"file": serialize_file(file, context.file_urls), "uploadSignedUrl": signed_url}


def _weak_etag_matches(header: str, etag: str) -> bool:
    if header.strip() == "*":
        return True
    target = etag.removeprefix("W/")
    return any(tag.strip().removeprefix("W/") == target for tag in header.split(","))


def _is_fresh(headers: Headers, etag: str, mtime: float) -> bool:
    """`fresh`：If-None-Match 优先，其次 If-Modified-Since；带 no-cache 时视为过期。"""
    if_none_match = headers.get("if-none-match")
    if_modified_since = headers.get("if-modified-since")
    if not if_none_match and not if_modified_since:
        return False
    if "no-cache" in (headers.get("cache-control") or ""):
        return False
    if if_none_match:
        return _weak_etag_matches(if_none_match, etag)
    try:
        since = parsedate_to_datetime(if_modified_since or "").timestamp()
    except TypeError, ValueError:
        return False
    return int(mtime) <= since


def _content_type(path: FsPath) -> str:
    extension = path.suffix.removeprefix(".").lower()
    return (
        _CONTENT_TYPES.get(extension)
        or mimetypes.guess_type(path.name)[0]
        or "application/octet-stream"
    )


def send_file(request: Request, path: FsPath) -> Response:
    """Express `res.sendFile`：弱 ETag、Last-Modified、Cache-Control，支持条件请求与 Range。"""
    try:
        stat = path.stat()
    except OSError:
        raise not_found({"error": "fileNotFound"}) from None
    etag = f'W/"{stat.st_size:x}-{stat.st_mtime_ns // 1_000_000:x}"'
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=0",
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
        "ETag": etag,
    }
    if _is_fresh(request.headers, etag, stat.st_mtime):
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type=_content_type(path), headers=headers, stat_result=stat)


def redirect(request: Request, url: str) -> Response:
    """Express `res.redirect`：302，正文随 Accept 选择 text / html。"""
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


def parse_uuid(value: str) -> UUID:
    """nestjs ParseUUIDPipe（version all）。"""
    if not _UUID_RE.fullmatch(value):
        raise bad_request("Validation failed (uuid is expected)")
    return UUID(value)


@files_router.endpoint(
    "GET",
    "/private/{id}",
    operation_id="readPrivateFile",
    auth=Auth.JWT,
    include_in_schema=False,
)
async def read_private_file(
    request: Request,
    id: Annotated[str, Path()],
    user: CurrentUser,
    context: Context,
    session: DbSession,
) -> Response:
    file = await FilesService(session).find_accessible(parse_uuid(id), user.id)
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
    file = await _find_public_by_file_name(session, context.settings.app.api_prefix, name)
    if file is None or name != path:
        raise not_found({"error": "fileNotFound"})
    return send_file(request, LOCAL_FILES_ROOT / name)
