"""multipart 单文件接收（nestjs FileInterceptor('file') + multer + busboy 的错误语义）。"""

import re
from dataclasses import dataclass, field
from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

import anyio
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

from app.common.exceptions import HttpError, bad_request
from app.modules.files.purposes import file_too_large_error
from app.modules.files.storage import LOCAL_FILES_ROOT

if TYPE_CHECKING:
    from collections.abc import Callable

    from anyio import AsyncFile
    from python_multipart.multipart import MultipartCallbacks
    from starlette.requests import Request

    from app.modules.files.storage import FileStorage

FILE_FIELD: Final = "file"
FIELD_SIZE_LIMIT: Final = 1024 * 1024
SPOOL_MAX_MEMORY: Final = 1024 * 1024
FIELD_NESTING_DEPTH: Final = 32
FIELD_ARRAY_INDEX_LIMIT: Final = 1000
_BRACKET_PATH: Final = re.compile(r"[^\[]+(?:\[[^\]]+\])*(?:\[\])?")
_ARRAY_INDEX: Final = re.compile(r"\[(\d+)\]", re.ASCII)
_FORM_DATA_ESCAPES: Final = re.compile(r"%0A|%0D|%22", re.IGNORECASE)
_FORM_DATA_UNESCAPED: Final = {"%0A": "\n", "%0D": "\r", "%22": '"'}


class FileSink(Protocol):
    key: str

    async def write(self, chunk: bytes) -> None: ...

    async def commit(self) -> None: ...

    async def discard(self) -> None: ...


class LocalSink:
    """写入 `./files/<key>`（multer diskStorage）。"""

    def __init__(self, key: str) -> None:
        self.key = key
        self.path = LOCAL_FILES_ROOT / key
        self.handle: AsyncFile[bytes] | None = None

    async def _open(self) -> AsyncFile[bytes]:
        if self.handle is None:
            await anyio.Path(LOCAL_FILES_ROOT).mkdir(parents=True, exist_ok=True)
            self.handle = await anyio.open_file(self.path, "wb")
        return self.handle

    async def write(self, chunk: bytes) -> None:
        await (await self._open()).write(chunk)

    async def commit(self) -> None:
        await (await self._open()).aclose()

    async def discard(self) -> None:
        if self.handle is not None:
            await self.handle.aclose()
        await anyio.Path(self.path).unlink(missing_ok=True)


class S3Sink:
    """先落到临时文件，接收完毕后上传到桶（multer-s3）。"""

    def __init__(self, storage: FileStorage, key: str, content_type: str) -> None:
        self.storage = storage
        self.key = key
        self.content_type = content_type
        self.buffer: SpooledTemporaryFile[bytes] = SpooledTemporaryFile(max_size=SPOOL_MAX_MEMORY)  # noqa: SIM115
        self.committed = False

    async def write(self, chunk: bytes) -> None:
        self.buffer.write(chunk)

    async def commit(self) -> None:
        self.buffer.seek(0)
        try:
            await self.storage.put(self.key, self.buffer, self.content_type)
        finally:
            self.buffer.close()
        self.committed = True

    async def discard(self) -> None:
        self.buffer.close()
        if self.committed:
            await self.storage.delete_quietly(self.key)


@dataclass(slots=True)
class ReceivedFile:
    original_name: str
    size: int
    sink: FileSink


type FileFilter = Callable[[str], None]
type SinkFactory = Callable[[str], FileSink]
"""参数为客户端文件名，返回以随机键命名的存储目标。"""


def _decode_param(raw: bytes) -> str:
    text = raw.decode("latin-1")
    return _FORM_DATA_ESCAPES.sub(lambda m: _FORM_DATA_UNESCAPED[m.group(0).upper()], text)


def _basename(file_name: str) -> str:
    return re.split(r"[/\\]", file_name)[-1]


def check_field_name(name: str) -> None:
    """@nestjs/platform-express 默认 limits 下 multer 对文本字段名的检查。"""
    if name.count("[") > FIELD_NESTING_DEPTH:
        raise bad_request(f"Field name nesting too deep - {name}")
    if _BRACKET_PATH.fullmatch(name) and any(
        int(index) > FIELD_ARRAY_INDEX_LIMIT for index in _ARRAY_INDEX.findall(name)
    ):
        raise bad_request(f"Field name array index too large - {name}")


def is_multipart(content_type: str | None) -> bool:
    if not content_type:
        return False
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type.startswith("multipart/")


@dataclass(slots=True)
class _Part:
    headers: dict[str, str] = field(default_factory=dict)
    header_field: bytearray = field(default_factory=bytearray)
    header_value: bytearray = field(default_factory=bytearray)
    kind: str = "skip"
    name: str = ""
    size: int = 0
    received: ReceivedFile | None = None


class _Events:
    """python-multipart 的同步回调先收集为事件，再在异步循环中处理。"""

    def __init__(self) -> None:
        self.queue: list[tuple[str, bytes]] = []
        self.ended = False

    def callbacks(self) -> dict[str, Any]:
        def record(name: str) -> Callable[..., None]:
            def callback(data: bytes | None = None, start: int = 0, end: int = 0) -> None:
                self.queue.append((name, bytes(data[start:end]) if data is not None else b""))

            return callback

        return {
            name: record(name)
            for name in (
                "on_part_begin",
                "on_header_field",
                "on_header_value",
                "on_header_end",
                "on_headers_finished",
                "on_part_data",
                "on_part_end",
                "on_end",
            )
        }


class _Receiver:
    def __init__(self, file_filter: FileFilter, open_sink: SinkFactory, max_file_size: int) -> None:
        self.file_filter = file_filter
        self.open_sink = open_sink
        self.max_file_size = max_file_size
        self.part = _Part()
        self.file: ReceivedFile | None = None
        self.open_files: list[ReceivedFile] = []

    def _classify(self, part: _Part) -> None:
        disposition = part.headers.get("content-disposition")
        if disposition is None:
            return
        kind, params = parse_options_header(disposition)
        if kind != b"form-data":
            return
        raw_name = params.get(b"name")
        if raw_name is None:
            raise bad_request("Field name missing")
        part.name = _decode_param(raw_name)
        raw_filename = params.get(b"filename")
        if raw_filename is None:
            part.kind = "field"
            return
        filename = _basename(_decode_param(raw_filename))
        if not filename:
            return
        if part.name != FILE_FIELD or self.file is not None:
            raise bad_request(f"Unexpected file field - {part.name}")
        self.file_filter(filename)
        received = ReceivedFile(filename, 0, self.open_sink(filename))
        self.open_files.append(received)
        self.file = received
        part.received = received
        part.kind = "file"

    async def handle(self, event: str, data: bytes) -> None:
        part = self.part
        if event == "on_part_begin":
            self.part = _Part()
        elif event == "on_header_field":
            part.header_field += data
        elif event == "on_header_value":
            part.header_value += data
        elif event == "on_header_end":
            name = part.header_field.decode("latin-1").strip().lower()
            part.headers.setdefault(name, part.header_value.decode("latin-1").strip())
            part.header_field.clear()
            part.header_value.clear()
        elif event == "on_headers_finished":
            self._classify(part)
        elif event == "on_part_data":
            part.size += len(data)
            if part.kind == "field" and part.size > FIELD_SIZE_LIMIT:
                raise bad_request(f"Field value too long - {part.name}")
            if part.kind == "file" and part.received is not None:
                if part.size > self.max_file_size:
                    raise file_too_large_error()
                part.received.size = part.size
                await part.received.sink.write(data)
        elif event == "on_part_end":
            if part.kind == "field":
                check_field_name(part.name)
            elif part.received is not None:
                await part.received.sink.commit()

    async def discard(self) -> None:
        for received in self.open_files:
            await received.sink.discard()


class _Preamble:
    """丢弃首个分隔符之前的内容（busboy 允许 preamble）。"""

    def __init__(self, boundary: bytes) -> None:
        self.delimiter = b"--" + boundary
        self.buffer = b""
        self.done = False

    def strip(self, chunk: bytes) -> bytes:
        if self.done:
            return chunk
        self.buffer += chunk
        if self.buffer.startswith(self.delimiter):
            index = 0
        else:
            found = self.buffer.find(b"\r\n" + self.delimiter)
            if found < 0:
                self.buffer = self.buffer[-(len(self.delimiter) + 1) :]
                return b""
            index = found + 2
        self.done = True
        data, self.buffer = self.buffer[index:], b""
        return data


def _parse_error(error: MultipartParseError) -> HttpError:
    if "header" in str(error).lower():
        return bad_request("Multipart: Malformed part header")
    return bad_request("Multipart: Unexpected end of form")


async def receive_single_file(
    request: Request,
    *,
    file_filter: FileFilter,
    open_sink: SinkFactory,
    max_file_size: int,
) -> ReceivedFile | None:
    """非 multipart 请求返回 None；出错时删除已写入的文件。"""
    content_type = request.headers.get("content-type")
    if not is_multipart(content_type):
        return None
    media_type, params = parse_options_header(content_type)
    if media_type != b"multipart/form-data":
        raise HttpError.build(415, "Unsupported content type")
    boundary = params.get(b"boundary")
    if not boundary:
        raise bad_request("Multipart: Boundary not found")
    events = _Events()
    callbacks = events.callbacks()
    parser = MultipartParser(boundary, cast("MultipartCallbacks", callbacks))
    receiver = _Receiver(file_filter, open_sink, max_file_size)
    preamble = _Preamble(boundary)
    try:
        async for received in request.stream():
            chunk = preamble.strip(received)
            if not chunk:
                continue
            try:
                parser.write(chunk)
            except MultipartParseError as error:
                raise _parse_error(error) from None
            for event, data in events.queue:
                if event == "on_end":
                    events.ended = True
                await receiver.handle(event, data)
            events.queue.clear()
        if not events.ended:
            raise bad_request("Multipart: Unexpected end of form")
    except BaseException:
        await receiver.discard()
        raise
    return receiver.file
