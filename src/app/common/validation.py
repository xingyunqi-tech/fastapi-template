"""Pydantic / FastAPI 校验错误 → 约定 §2.3 `data.errors`。"""

import logging
import re
from typing import TYPE_CHECKING, Any, Final, get_args

from pydantic_core.core_schema import ErrorType

from app.common.errors import FieldError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

logger = logging.getLogger("app.validation")

BUILTIN_ERROR_TYPES: Final = frozenset(get_args(ErrorType))
PREFIXED_LOCATIONS: Final = frozenset({"query", "path", "header", "cookie"})
_QUOTED: Final = re.compile(r"'((?:[^'\\]|\\.)*)'")
_SCALAR_TAGS: Final = frozenset(
    {"int", "float", "str", "bool", "bytes", "none", "decimal", "uuid", "date", "datetime", "time"}
)

_EXACT: Final[Mapping[str, str]] = {
    "missing": "required",
    "uuid_type": "uuid",
    "uuid_parsing": "uuid",
    "uuid_version": "uuid",
    "url_type": "url",
    "url_parsing": "url",
    "url_scheme": "url",
    "url_too_long": "url",
    "enum": "enum",
    "literal_error": "enum",
    "too_short": "minItems",
    "too_long": "maxItems",
    "string_too_short": "minLength",
    "string_too_long": "maxLength",
    "greater_than": "min",
    "greater_than_equal": "min",
    "less_than": "max",
    "less_than_equal": "max",
    "string_pattern_mismatch": "pattern",
    "int_from_float": "type",
}

_PARAMS: Final[Mapping[str, tuple[str, ...]]] = {
    "min": ("ge", "gt"),
    "max": ("le", "lt"),
    "minLength": ("min_length",),
    "maxLength": ("max_length",),
    "minItems": ("min_length",),
    "maxItems": ("max_length",),
    "pattern": ("pattern",),
}


def field_path(loc: Sequence[str | int]) -> str:
    """请求体字段去掉 `body` 前缀，查询 / 路径参数加 `query.` / `path.`；下标写成 `[i]`。"""
    parts = list(loc)
    prefix = ""
    if parts and parts[0] == "body":
        parts = parts[1:]
        if not parts:
            return "body"
    elif parts and parts[0] in PREFIXED_LOCATIONS:
        prefix = str(parts[0])
        parts = parts[1:]
    path = ""
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    if prefix:
        return f"{prefix}.{path}" if path else prefix
    return path


def _rule(error_type: str) -> str | None:
    if error_type in _EXACT:
        return _EXACT[error_type]
    if error_type not in BUILTIN_ERROR_TYPES:
        return error_type
    if error_type.endswith(("_type", "_parsing")):
        return "type"
    return None


def _json_value(value: Any) -> Any:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _params(rule: str, error: Mapping[str, Any]) -> dict[str, Any] | None:
    ctx: Mapping[str, Any] = error.get("ctx") or {}
    exclusive = {"gt": 1, "lt": -1}
    for name, step in exclusive.items():
        bound = ctx.get(name)
        if isinstance(bound, int) and not isinstance(bound, bool):
            return {rule: bound + step}
    if rule == "enum":
        expected = ctx.get("expected")
        values = _QUOTED.findall(expected) if isinstance(expected, str) else []
        return {"values": values} if values else None
    names = _PARAMS.get(rule)
    if names is not None:
        for name in names:
            if name in ctx:
                return {rule: _json_value(ctx[name])}
        return None
    if error.get("type") not in BUILTIN_ERROR_TYPES and ctx:
        return {key: _json_value(value) for key, value in ctx.items()}
    return None


def to_field_error(error: Mapping[str, Any]) -> FieldError:
    error_type = str(error.get("type", ""))
    field = field_path(error.get("loc", ()))
    rule = _rule(error_type)
    if rule is None:
        logger.warning("Unmapped validation error type %s at %s", error_type, field)
        return FieldError(field, "format")
    return FieldError(field, rule, _params(rule, error))


def is_malformed_json(errors: Iterable[Mapping[str, Any]]) -> bool:
    return any(error.get("type") == "json_invalid" for error in errors)


def _is_union_tag(part: str | int) -> bool:
    """Pydantic 联合类型分支标签：类型名（`int`、`list[int]`）或模型类名（大写开头）。"""
    return isinstance(part, str) and (part in _SCALAR_TAGS or "[" in part or part[:1].isupper())


def _strip_union_tags(errors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """同一前缀下出现两个以上不同分支标签时，去掉该位置，使 `field` 为 JSON 路径。"""
    items = [dict(error, loc=tuple(error.get("loc", ()))) for error in errors]
    position = 0
    while True:
        tags: dict[tuple[Any, ...], set[str | int]] = {}
        for item in items:
            loc = item["loc"]
            if len(loc) > position and _is_union_tag(loc[position]):
                tags.setdefault(loc[:position], set()).add(loc[position])
        union_prefixes = {prefix for prefix, found in tags.items() if len(found) > 1}
        longest = max((len(item["loc"]) for item in items), default=0)
        for item in items:
            loc = item["loc"]
            if len(loc) > position and loc[:position] in union_prefixes:
                item["loc"] = loc[:position] + loc[position + 1 :]
        if not union_prefixes:
            position += 1
            if position >= longest:
                return items


def field_errors(errors: Iterable[Mapping[str, Any]]) -> list[FieldError]:
    """每个字段只保留第一条错误。"""
    seen: set[str] = set()
    result: list[FieldError] = []
    for error in _strip_union_tags(list(errors)):
        item = to_field_error(error)
        if item.field in seen:
            continue
        seen.add(item.field)
        result.append(item)
    return result
