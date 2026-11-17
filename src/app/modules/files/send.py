"""Express `res.sendFile`（send 1.2.1、range-parser 1.3.0、fresh 2.0.0）的条件请求与 Range 语义。"""

import re
from dataclasses import dataclass
from email.utils import formatdate, parsedate_to_datetime
from typing import TYPE_CHECKING, Final

import anyio
from starlette.responses import Response

from app.common.exceptions import HttpError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from os import stat_result
    from pathlib import Path

    from starlette.datastructures import Headers
    from starlette.types import Receive, Scope, Send

RANGE_INVALID: Final = -2
RANGE_UNSATISFIABLE: Final = -1
CHUNK_SIZE: Final = 64 * 1024

_BYTES_RANGE: Final = re.compile(r"^ *bytes=")
_DIGITS: Final = re.compile(r"[0-9]+")
_NO_CACHE: Final = re.compile(r"(?:^|,)\s*?no-cache\s*?(?:,|$)")
_JOINED_HEADERS: Final = ("range", "if-match", "if-none-match", "if-range", "cache-control")
_FIRST_HEADERS: Final = ("if-modified-since", "if-unmodified-since")
_JS_WHITESPACE: Final = " \t\n\v\f\r\xa0"


@dataclass(frozen=True, slots=True)
class ByteRange:
    start: int
    end: int


def _parse_pos(value: str) -> int | None:
    return int(value) if _DIGITS.fullmatch(value) else None


def parse_range(size: int, header: str) -> list[ByteRange] | int:
    """range-parser（combine）：畸形为 `RANGE_INVALID`，无可满足区间为 `RANGE_UNSATISFIABLE`。"""
    index = header.find("=")
    if index == -1:
        return RANGE_INVALID
    ranges: list[ByteRange] = []
    for part in header[index + 1 :].split(","):
        dash = part.find("-")
        if dash == -1:
            return RANGE_INVALID
        start_str = part[:dash].strip(_JS_WHITESPACE)
        end_str = part[dash + 1 :].strip(_JS_WHITESPACE)
        start, end = _parse_pos(start_str), _parse_pos(end_str)
        if not start_str:
            start, end = (None if end is None else size - end), size - 1
        elif not end_str:
            end = size - 1
        if end is not None:
            end = min(end, size - 1)
        if start is None or end is None:
            return RANGE_INVALID
        if start > end or start < 0:
            continue
        ranges.append(ByteRange(start, end))
    if not ranges:
        return RANGE_UNSATISFIABLE
    return _combine(ranges)


def _combine(ranges: list[ByteRange]) -> list[ByteRange]:
    ordered = sorted(([r.start, r.end, i] for i, r in enumerate(ranges)), key=lambda r: r[0])
    merged = [ordered[0]]
    for start, end, index in ordered[1:]:
        current = merged[-1]
        if start > current[1] + 1:
            merged.append([start, end, index])
        elif end > current[1]:
            current[1] = end
            current[2] = min(current[2], index)
    return [ByteRange(start, end) for start, end, _ in sorted(merged, key=lambda r: r[2])]


def _parse_token_list(value: str) -> list[str]:
    tokens: list[str] = []
    start = end = 0
    for i, char in enumerate(value):
        if char == " ":
            if start == end:
                start = end = i + 1
        elif char == ",":
            if start != end:
                tokens.append(value[start:end])
            start = end = i + 1
        else:
            end = i + 1
    if start != end:
        tokens.append(value[start:end])
    return tokens


def _parse_http_date(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except TypeError, ValueError:
        return None


def _etag_matches(token: str, etag: str) -> bool:
    return token in (etag, "W/" + etag) or "W/" + token == etag


def _precondition_failed(headers: Mapping[str, str], etag: str, last_modified: float) -> bool:
    if match := headers.get("if-match"):
        return match != "*" and not any(
            _etag_matches(token, etag) for token in _parse_token_list(match)
        )
    since = _parse_http_date(headers.get("if-unmodified-since"))
    return since is not None and last_modified > since


def _fresh(headers: Mapping[str, str], etag: str, last_modified: float) -> bool:
    modified_since = headers.get("if-modified-since")
    none_match = headers.get("if-none-match")
    if not modified_since and not none_match:
        return False
    if _NO_CACHE.search(headers.get("cache-control") or ""):
        return False
    if (
        none_match
        and none_match != "*"
        and not any(_etag_matches(token, etag) for token in _parse_token_list(none_match))
    ):
        return False
    if modified_since:
        since = _parse_http_date(modified_since)
        if since is None or not last_modified <= since:
            return False
    return True


def _range_fresh(headers: Mapping[str, str], etag: str, last_modified: float) -> bool:
    if_range = headers.get("if-range")
    if not if_range:
        return True
    if '"' in if_range:
        return etag in if_range
    since = _parse_http_date(if_range)
    return since is not None and last_modified <= since


class FileSliceResponse(Response):
    def __init__(
        self,
        path: Path,
        offset: int,
        length: int,
        status_code: int,
        headers: Mapping[str, str],
        media_type: str,
    ) -> None:
        super().__init__(
            status_code=status_code,
            headers={**headers, "content-length": str(length)},
            media_type=media_type,
        )
        self.path = path
        self.offset = offset
        self.length = length

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await send(
            {"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers}
        )
        if scope["method"] == "HEAD" or self.length == 0:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        remaining = self.length
        async with await anyio.open_file(self.path, "rb") as file:
            await file.seek(self.offset)
            while remaining > 0:
                chunk = await file.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


def _node_headers(headers: Headers) -> dict[str, str]:
    joined = {name: ", ".join(headers.getlist(name)) for name in _JOINED_HEADERS}
    first = {name: headers.get(name) or "" for name in _FIRST_HEADERS}
    return {name: value for name, value in {**joined, **first}.items() if value}


def file_response(
    request_headers: Headers, path: Path, stat: stat_result, media_type: str
) -> Response:
    """412 / 304 条件请求、416 或单区间 206，其余返回全文；多区间按全文返回。"""
    headers = _node_headers(request_headers)
    size = stat.st_size
    last_modified = float(int(stat.st_mtime))
    etag = f'W/"{size:x}-{stat.st_mtime_ns // 1_000_000:x}"'
    base = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=0",
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
        "ETag": etag,
    }
    if any(
        headers.get(name)
        for name in ("if-match", "if-unmodified-since", "if-none-match", "if-modified-since")
    ):
        if _precondition_failed(headers, etag, last_modified):
            raise HttpError(412, "Precondition Failed", headers=base)
        if _fresh(headers, etag, last_modified):
            return Response(status_code=304, headers=base)

    offset, length, status = 0, size, 200
    range_header = headers.get("range") or ""
    if _BYTES_RANGE.match(range_header):
        ranges = parse_range(size, range_header)
        if not _range_fresh(headers, etag, last_modified):
            ranges = RANGE_INVALID
        if ranges == RANGE_UNSATISFIABLE:
            raise HttpError(
                416, "Range Not Satisfiable", headers={**base, "Content-Range": f"bytes */{size}"}
            )
        if isinstance(ranges, list) and len(ranges) == 1:
            byte_range = ranges[0]
            status = 206
            offset, length = byte_range.start, byte_range.end - byte_range.start + 1
            base["Content-Range"] = f"bytes {byte_range.start}-{byte_range.end}/{size}"
    return FileSliceResponse(path, offset, length, status, base, media_type)
