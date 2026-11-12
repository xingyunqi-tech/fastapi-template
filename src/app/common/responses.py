"""与 Express `res.json` 一致的 JSON 输出，以及 nestjs ResponseInterceptor 的统一信封。"""

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

import orjson
from starlette.responses import Response

from app.common.validation.js import MAX_SAFE_INTEGER, number_to_string

JSON_MEDIA_TYPE: Final = "application/json; charset=utf-8"
_MAX_ARRAY_INDEX: Final = 2**32 - 2


def _is_array_index(key: str) -> bool:
    return (
        key.isascii()
        and key.isdigit()
        and (key == "0" or key[0] != "0")
        and int(key) <= _MAX_ARRAY_INDEX
    )


def to_json_value(value: Any) -> Any:
    """JSON.stringify 语义：数组下标形式的键排在前面，数字与时间按 JS 格式输出。"""
    if isinstance(value, Mapping):
        items = [(str(k), to_json_value(v)) for k, v in value.items()]
        index_keys = sorted((k for k, _ in items if _is_array_index(k)), key=int)
        if not index_keys:
            return dict(items)
        lookup = dict(items)
        ordered = {k: lookup[k] for k in index_keys}
        ordered.update((k, v) for k, v in items if not _is_array_index(k))
        return ordered
    if isinstance(value, list | tuple):
        return [to_json_value(v) for v in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value == int(value) and abs(value) <= MAX_SAFE_INTEGER:
            return int(value)
        return orjson.Fragment(number_to_string(value).encode())
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > MAX_SAFE_INTEGER:
        return orjson.Fragment(number_to_string(value).encode())
    if isinstance(value, datetime):
        return iso_timestamp(value)
    return value


def iso_timestamp(value: datetime) -> str:
    """Date.prototype.toISOString：UTC、毫秒、以 Z 结尾。"""
    utc = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def dumps(content: Any) -> bytes:
    return orjson.dumps(to_json_value(content))


class JsonResponse(Response):
    media_type = JSON_MEDIA_TYPE

    def render(self, content: Any) -> bytes:
        return dumps(content)


def is_api_response(value: Any) -> bool:
    """nestjs ResponseInterceptor.isApiResponse：已是信封的返回原样透传。"""
    if not isinstance(value, Mapping):
        return False
    code = value.get("code")
    return (
        isinstance(code, int | float)
        and not isinstance(code, bool)
        and 100 <= code < 600
        and isinstance(value.get("message"), str)
        and "data" in value
    )


def envelope(data: Any = None, *, include_data: bool = True) -> dict[str, Any]:
    body: dict[str, Any] = {"code": 200, "message": "success"}
    if include_data:
        body["data"] = data
    return body
