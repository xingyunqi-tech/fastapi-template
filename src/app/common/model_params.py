"""按 Pydantic 约束校验不在 validation.json 中的 DTO（如生成的模块），失败返回 422。"""

import json
from typing import Any
lazy from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from pydantic import BaseModel, ValidationError

from app.common.exceptions import validation_errors
from app.common.params import parse_query_string, read_json_body
from app.common.validation.js import is_undefined, stringify


def _validate[M: BaseModel](model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        messages: dict[str, list[str]] = {}
        for item in error.errors():
            field = str(item["loc"][0]) if item["loc"] else "body"
            messages.setdefault(field, []).append(
                f"{field} {item['msg'][:1].lower()}{item['msg'][1:]}"
            )
        raise validation_errors({k: ", ".join(v) for k, v in messages.items()}) from None


def model_body[M: BaseModel](model: type[M]) -> Callable[[Request], Awaitable[M]]:
    async def dependency(request: Request) -> M:
        raw = await read_json_body(request)
        data = {} if is_undefined(raw) or raw is None else json.loads(stringify(raw))
        return _validate(model, data)

    return dependency


def model_query[M: BaseModel](model: type[M]) -> Callable[[Request], Awaitable[M]]:
    async def dependency(request: Request) -> M:
        query = parse_query_string(request.scope.get("query_string", b"").decode("latin-1"))
        return _validate(model, {k: v for k, v in query.items() if v != ""})

    return dependency


def ModelBody[M: BaseModel](model: type[M]) -> Any:  # noqa: N802
    return Depends(model_body(model))


def ModelQuery[M: BaseModel](model: type[M]) -> Any:  # noqa: N802
    return Depends(model_query(model))
