"""请求体、查询参数、路径参数的解析与校验（nestjs body-parser、ValidationPipe、ParseIdPipe）。"""

import re
from functools import cache
from typing import TYPE_CHECKING, Annotated, Any, Final
from urllib.parse import unquote

from fastapi import Depends, Path, Request
from pydantic import BaseModel

from app.common.exceptions import bad_request
from app.common.middleware import parsed_json_body
from app.common.routing import decode_uri_component
from app.common.validation import metadata
from app.common.validation.engine import Validator, run_pipe
from app.common.validation.js import JsValue, stringify

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

QUERY_MAX_KEYS: Final = 1000
_ID_RE: Final = re.compile(r"\d+", re.ASCII)
MAX_ID: Final = 2_147_483_647


BODY_MODELS: Final[dict[Callable[..., Any], type[BaseModel]]] = {}
QUERY_MODELS: Final[dict[Callable[..., Any], type[BaseModel]]] = {}


@cache
def get_validator() -> Validator:
    return Validator(metadata.load())


async def read_json_body(request: Request) -> JsValue:
    """JsonBodyMiddleware 已解析的请求体；非 JSON 请求为 undefined。"""
    return parsed_json_body(request.scope)


def _unescape(text: str) -> str:
    try:
        return decode_uri_component(text)
    except ValueError:
        return unquote(text, encoding="latin-1")


def parse_query_string(query: str, max_keys: int = QUERY_MAX_KEYS) -> dict[str, Any]:
    """Node querystring.parse：`+` 视为空格，重复键合并为数组，最多解析 1000 对。"""
    result: dict[str, Any] = {}
    if not query:
        return result
    for pair in query.split("&")[:max_keys]:
        if not pair:
            continue
        key, _, value = pair.partition("=")
        name = _unescape(key.replace("+", " "))
        decoded = _unescape(value.replace("+", " "))
        if name in result:
            existing = result[name]
            result[name] = (
                [*existing, decoded] if isinstance(existing, list) else [existing, decoded]
            )
        else:
            result[name] = decoded
    return result


def _query_object(request: Request) -> dict[str, Any]:
    return parse_query_string(request.scope.get("query_string", b"").decode("latin-1"))


def _to_model[M: BaseModel](model: type[M], value: JsValue) -> M:
    return model.model_validate_json(stringify(value), by_alias=True, by_name=False)


def validated_body[M: BaseModel](model: type[M]) -> Callable[[Request], Awaitable[M]]:
    """以模型类名（与 nestjs DTO 同名）查 validation.json 校验请求体。"""

    async def dependency(request: Request) -> M:
        raw = await read_json_body(request)
        value = run_pipe(get_validator(), model.__name__, raw, "body")
        return _to_model(model, value)

    BODY_MODELS[dependency] = model
    return dependency


def validated_query[M: BaseModel](model: type[M]) -> Callable[[Request], Awaitable[M]]:
    async def dependency(request: Request) -> M:
        value = run_pipe(get_validator(), model.__name__, _query_object(request), "query")
        return _to_model(model, value)

    QUERY_MODELS[dependency] = model
    return dependency


def Body[M: BaseModel](model: type[M]) -> Any:  # noqa: N802
    return Depends(validated_body(model))


def Query[M: BaseModel](model: type[M]) -> Any:  # noqa: N802
    return Depends(validated_query(model))


def parse_id(value: str) -> int:
    """nestjs ParseIdPipe：`^\\d+$` 且 1 ≤ id ≤ 2147483647，否则 400 `{id: invalidId}`。"""
    if not _ID_RE.fullmatch(value) or not 1 <= int(value) <= MAX_ID:
        raise bad_request({"status": 400, "errors": {"id": "invalidId"}})
    return int(value)


async def _id_param(id: Annotated[str, Path()]) -> int:
    return parse_id(id)


IdParam = Annotated[int, Depends(_id_param)]

__all__ = ["Body", "IdParam", "Query", "read_json_body"]
