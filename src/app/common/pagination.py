"""约定 §3 分页与排序。"""

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Annotated, Any, Final, Self
lazy from collections.abc import Callable, Sequence

from fastapi import Query
from fastapi.exceptions import RequestValidationError
from pydantic import Field, TypeAdapter, ValidationError

from app.common.types import INT32_MAX, ApiModel

MAX_PAGE_SIZE: Final = 100
DEFAULT_PAGE_SIZE: Final = 20


class PageParams(ApiModel):
    strict_scalars = False

    page: int = Field(default=1, ge=1, le=INT32_MAX)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page[T](ApiModel):
    """接口返回值用具名子类（`class UserPage(Page[User])`），OpenAPI 组件名即类名。"""

    items: list[T]
    total: int
    page: int
    page_size: int

    @classmethod
    def of(cls, items: Sequence[T], total: int, params: PageParams) -> Self:
        return cls(items=list(items), total=total, page=params.page, page_size=params.page_size)


def _invalid(field: str, error_type: str, message: str, **ctx: Any) -> RequestValidationError:
    return RequestValidationError(
        [{"type": error_type, "loc": ("query", field), "msg": message, "ctx": ctx}]
    )


def invalid_cursor() -> RequestValidationError:
    return _invalid("cursor", "format", "invalid cursor")


def encode_cursor(sort_key: Any, row_id: Any) -> str:
    raw = json.dumps([sort_key, row_id], default=str, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class CursorParams(ApiModel):
    strict_scalars = False

    cursor: str | None = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    def decoded(self, key_type: Any = Any, id_type: Any = Any) -> tuple[Any, Any] | None:
        """`(sort_key, id)` 按给定类型校验；首页为 None，非法游标抛 `query.cursor` 校验错误。"""
        if self.cursor is None:
            return None
        try:
            padded = self.cursor + "=" * (-len(self.cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded.encode()))
        except binascii.Error, ValueError, UnicodeDecodeError:
            raise _invalid("cursor", "format", "invalid cursor") from None
        if not isinstance(value, list) or len(value) != 2:
            raise _invalid("cursor", "format", "invalid cursor")
        try:
            key = TypeAdapter(key_type).validate_python(value[0], strict=True)
            id_ = TypeAdapter(id_type).validate_python(value[1], strict=True)
        except ValidationError:
            raise _invalid("cursor", "format", "invalid cursor") from None
        return key, id_


class CursorPage[T](ApiModel):
    items: list[T]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SortField:
    field: str
    descending: bool


def parse_sort(value: str, allowed: Sequence[str]) -> list[SortField]:
    """`field,-field2`；字段不在白名单或重复时抛 `query.sort` 的 enum 错误。"""
    fields: list[SortField] = []
    seen: set[str] = set()
    expected = " or ".join(repr(name) for name in allowed)
    for raw in value.split(","):
        item = raw.strip()
        descending = item.startswith("-")
        name = item.removeprefix("-")
        if name not in allowed or name in seen:
            raise _invalid("sort", "enum", f"Input should be {expected}", expected=expected)
        seen.add(name)
        fields.append(SortField(name, descending))
    return fields


def sort_param(
    allowed: Sequence[str], default: str | None = None
) -> Callable[..., list[SortField]]:
    """`sort` 查询参数依赖，用法 `Annotated[list[SortField], Depends(sort_param(...))]`。"""
    allowed = tuple(allowed)
    description = "排序字段，逗号分隔，`-` 前缀为降序；可选 " + "、".join(allowed)

    def dependency(
        sort: Annotated[str | None, Query(description=description)] = default,
    ) -> list[SortField]:
        value = sort or default
        return parse_sort(value, allowed) if value else []

    return dependency
