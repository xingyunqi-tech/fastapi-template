"""nestjs DTO 中 @Transform 与 @ValidateIf 函数的 Python 实现，按规范化后的 JS 源码登记。"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.common.validation.js import (
    UNDEFINED,
    JsValue,
    is_nullish,
    parse_json,
    to_number,
    to_string,
    trim,
    truthy,
)
from app.common.validation.metadata import normalize_source


@dataclass(frozen=True, slots=True)
class TransformContext:
    obj: dict[str, Any]
    key: str
    type_target: str | None
    to_instance: Callable[[str, JsValue], JsValue]


type Transform = Callable[[JsValue, TransformContext], JsValue]
type Condition = Callable[[dict[str, Any], JsValue], bool]

_TAG_RE = re.compile(r"<[^>]*>")


def _number_or(default: int) -> Transform:
    return lambda value, _: to_number(value) if truthy(value) else default


def _lower(value: JsValue, _: TransformContext) -> JsValue:
    return value.lower() if isinstance(value, str) else value


def _upper(value: JsValue, _: TransformContext) -> JsValue:
    return value.upper() if isinstance(value, str) else value


def _trim(value: JsValue, _: TransformContext) -> JsValue:
    return trim(value) if isinstance(value, str) else value


def _lower_trim_optional(value: JsValue, _: TransformContext) -> JsValue:
    if is_nullish(value):
        return UNDEFINED
    if not isinstance(value, str):
        raise TypeError("params.value?.toLowerCase is not a function")
    return trim(value.lower())


def _sanitize(value: JsValue, _: TransformContext) -> JsValue:
    if not isinstance(value, str):
        return value
    return trim(_TAG_RE.sub("", value))


def _lower_each(value: JsValue, _: TransformContext) -> JsValue:
    if not isinstance(value, list):
        return value
    return [item.lower() if isinstance(item, str) else item for item in value]


def _string_bool(value: JsValue, _: TransformContext) -> JsValue:
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def _trim_or_undefined(value: JsValue, _: TransformContext) -> JsValue:
    if not isinstance(value, str):
        return value
    return trim(value) or UNDEFINED


def _trim_or_null(value: JsValue, _: TransformContext) -> JsValue:
    if not isinstance(value, str):
        return value
    return trim(value) or None


def _json_query(value: JsValue, ctx: TransformContext) -> JsValue:
    if not truthy(value):
        return UNDEFINED
    if ctx.type_target is None:
        raise LookupError(f"{ctx.key} 的 JSON 查询转换缺少 @Type 目标类")
    try:
        parsed = parse_json(to_string(value))
    except ValueError:
        return value
    return ctx.to_instance(ctx.type_target, parsed)


def _split_list(value: JsValue, _: TransformContext) -> JsValue:
    items = value if isinstance(value, list) else [value]
    parts = [trim(part) for item in items for part in to_string(item).split(",")]
    return [part for part in parts if part]


def _blank_to(default: JsValue, *, null_blank: bool = False) -> Transform:
    def transform(value: JsValue, _: TransformContext) -> JsValue:
        if value is UNDEFINED or value == "" or (null_blank and value is None):
            return default
        return to_number(value)

    return transform


def _source(text: str) -> str:
    return normalize_source(text)


TRANSFORMS: dict[str, Transform] = {
    _source("({ value }) => (value ? Number(value) : 1)"): _number_or(1),
    _source("({ value }) => (value ? Number(value) : 10)"): _number_or(10),
    _source("({ value }) => (value ? Number(value) : 20)"): _number_or(20),
    _source("({ value }) => (value ? Number(value) : 50)"): _number_or(50),
    _source("({ value }) => (typeof value === 'string' ? value.toLowerCase() : value)"): _lower,
    _source("({ value }) => typeof value === 'string' ? value.toLowerCase() : value"): _lower,
    _source("(params) => params.value?.toLowerCase().trim()"): _lower_trim_optional,
    _source(
        "(params) => { if (typeof params.value !== 'string') { return params.value; } "
        "return params.value .replace(/<[^>]*>/g, '') .trim(); }"
    ): _sanitize,
    _source(
        "({ value }) => Array.isArray(value) ? value.map((item) => "
        "(typeof item === 'string' ? item.toLowerCase() : item)) : value"
    ): _lower_each,
    _source("({ value }) => (typeof value === 'string' ? value.trim() : value)"): _trim,
    _source("({ value }) => value === 'true' ? true : value === 'false' ? false : value"): (
        _string_bool
    ),
    _source("({ value }) => (value === 'true' ? true : value === 'false' ? false : value)"): (
        _string_bool
    ),
    _source(
        "({ value }) => { if (value === 'true') return true; "
        "if (value === 'false') return false; return value; }"
    ): _string_bool,
    _source("({ value }) => typeof value === 'string' ? value.trim() || undefined : value"): (
        _trim_or_undefined
    ),
    _source("({ value }) => (typeof value === 'string' ? value.trim() || undefined : value)"): (
        _trim_or_undefined
    ),
    _source("({ value }) => typeof value === 'string' ? value.trim() || null : value"): (
        _trim_or_null
    ),
    _source(
        "({ value }) => { if (!value) { return undefined; } try { return "
        "(0, class_transformer_1.plainToInstance)(cls, JSON.parse(value)); } "
        "catch { return value; } }"
    ): _json_query,
    _source(
        "({ value }) => [] .concat(value) .flatMap((item) => String(item).split(',')) "
        ".map((item) => item.trim()) .filter(Boolean)"
    ): _split_list,
    _source("({ value }) => (value === undefined || value === '' ? 0 : Number(value))"): (
        _blank_to(0)
    ),
    _source(
        "({ value }) => (value === undefined || value === '' ? "
        "exports.SEGMENT_LIMITS.defaultSize : Number(value))"
    ): _blank_to(10),
    _source(
        "({ value }) => value === '' || value === undefined || value === null "
        "? undefined : Number(value)"
    ): _blank_to(UNDEFINED, null_blank=True),
    _source("({ value }) => value === undefined || value === '' ? undefined : Number(value)"): (
        _blank_to(UNDEFINED)
    ),
    _source("({ value }) => typeof value === 'string' ? value.toUpperCase() : value"): _upper,
}


def _all_region_codes(obj: dict[str, Any]) -> bool:
    return all(
        truthy(obj.get(key, UNDEFINED)) for key in ("provinceCode", "cityCode", "districtCode")
    )


CONDITIONS: dict[str, Condition] = {
    _source(
        "(object, value) => { return object[propertyName] !== null "
        "&& object[propertyName] !== undefined; }"
    ): lambda _, value: not is_nullish(value),
    _source("(_, value) => value !== undefined"): lambda _, value: value is not UNDEFINED,
    _source("(_, value) => value !== null"): lambda _, value: value is not None,
    _source("(_, value) => value !== undefined && value !== null"): (
        lambda _, value: not is_nullish(value)
    ),
    _source("(dto) => dto.knowledgePointIds !== undefined"): (
        lambda dto, _: dto.get("knowledgePointIds", UNDEFINED) is not UNDEFINED
    ),
    _source("(obj) => !hasAllRegionCodes(obj)"): lambda obj, _: not _all_region_codes(obj),
    _source(
        "(dto) => dto.reason === feedback_1.QuestionReportReason.OTHER || dto.content !== undefined"
    ): lambda dto, _: (
        dto.get("reason", UNDEFINED) == "other" or dto.get("content", UNDEFINED) is not UNDEFINED
    ),
    _source("(dto) => dto.feedback !== null"): (
        lambda dto, _: dto.get("feedback", UNDEFINED) is not None
    ),
    _source("(dto) => dto.errataId !== null"): (
        lambda dto, _: dto.get("errataId", UNDEFINED) is not None
    ),
}
