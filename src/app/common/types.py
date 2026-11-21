"""约定 §4 数据类型与请求 / 响应模型基类。"""

import re
from datetime import UTC, date, datetime
from typing import Annotated, Any, ClassVar, Final

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    PlainSerializer,
    Strict,
    WithJsonSchema,
)
from pydantic.alias_generators import to_camel
from pydantic.types import UUID7
from pydantic_core import CoreSchema, PydanticCustomError

_EMAIL_RE: Final = re.compile(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+")
_EMAIL_MAX_LENGTH: Final = 254
_CN_MOBILE_RE: Final = re.compile(r"1[3-9][0-9]{9}")
_E164_RE: Final = re.compile(r"\+[1-9][0-9]{6,14}")
_STRICT_SCALARS: Final = frozenset({"int", "float", "bool"})
_OFFSET_DATETIME_RE: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?"
    r"(?:[Zz]|[+-][0-9]{2}:[0-9]{2})"
)
INT32_MAX: Final = 2**31 - 1


def _strict_scalars(schema: Any) -> None:
    if isinstance(schema, dict):
        kind = schema.get("type")
        if isinstance(kind, str) and kind in _STRICT_SCALARS:
            schema["strict"] = True
        for value in schema.values():
            _strict_scalars(value)
    elif isinstance(schema, list):
        for item in schema:
            _strict_scalars(item)


class ApiModel(BaseModel):
    """lowerCamelCase、忽略未知字段；`int` / `float` / `bool` 不做类型转换（查询参数模型关闭）。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="ignore",
        from_attributes=True,
        allow_inf_nan=False,
    )
    strict_scalars: ClassVar[bool] = True

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type[BaseModel], handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        schema = handler(source)
        if cls.strict_scalars:
            _strict_scalars(schema)
        return schema


def to_utc(value: datetime) -> datetime:
    """无时区的值视为 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    return to_utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _require_offset(value: Any) -> Any:
    """字符串输入须为带时区偏移的 RFC 3339；`datetime` 对象原样通过。"""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise PydanticCustomError("datetime_type", "Input should be an RFC 3339 string")
    if not _OFFSET_DATETIME_RE.fullmatch(value):
        raise PydanticCustomError("format", "Input should be an RFC 3339 date-time with offset")
    return value


UtcDatetime = Annotated[
    datetime,
    BeforeValidator(_require_offset),
    AfterValidator(to_utc),
    PlainSerializer(format_utc, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]

Date = Annotated[date, WithJsonSchema({"type": "string", "format": "date"})]


def _normalize_email(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


def _check_email(value: str) -> str:
    if len(value) > _EMAIL_MAX_LENGTH or not _EMAIL_RE.fullmatch(value):
        raise PydanticCustomError("email", "value is not a valid email address")
    return value


Email = Annotated[
    str,
    BeforeValidator(_normalize_email),
    AfterValidator(_check_email),
    WithJsonSchema({"type": "string", "format": "email"}),
]


def normalize_phone(value: str) -> str:
    """E.164 原样返回，11 位中国大陆手机号补 `+86`，其余抛 `phone` 错误。"""
    text = value.strip()
    if _CN_MOBILE_RE.fullmatch(text):
        return f"+86{text}"
    if _E164_RE.fullmatch(text):
        return text
    raise PydanticCustomError("phone", "value is not a valid phone number")


Phone = Annotated[
    str,
    AfterValidator(normalize_phone),
    WithJsonSchema({"type": "string", "pattern": _E164_RE.pattern}),
]

UUIDv7 = UUID7

Money = Annotated[int, Strict()]

SerialId = Annotated[int, Field(ge=1, le=INT32_MAX)]
