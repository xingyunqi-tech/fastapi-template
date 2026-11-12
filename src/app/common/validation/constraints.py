"""class-validator 0.15 内置约束与 nestjs 自定义约束，按导出的约束键注册。"""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.common.validation import validator_js
from app.common.validation.js import (
    UNDEFINED,
    JsBuiltinFunction,
    JsDate,
    JsRegExp,
    JsValue,
    is_nullish,
    is_number,
    number_to_string,
    strict_equals,
    stringify,
    truthy,
)

type Validate = Callable[[JsValue, list[Any]], bool]
type DynamicMessage = Callable[[JsValue, list[Any], str], str]


@dataclass(frozen=True, slots=True)
class Constraint:
    validate: Validate
    message: DynamicMessage | None = None


def _options(constraints: list[Any], index: int = 0) -> Mapping[str, Any] | None:
    value = constraints[index] if len(constraints) > index else None
    return value if isinstance(value, Mapping) else None


def _is_defined(value: JsValue, _: list[Any]) -> bool:
    return not is_nullish(value)


def _is_not_empty(value: JsValue, _: list[Any]) -> bool:
    return not (isinstance(value, str) and value == "") and not is_nullish(value)


def _is_string(value: JsValue, _: list[Any]) -> bool:
    return isinstance(value, str)


def _is_int(value: JsValue, _: list[Any]) -> bool:
    return is_number(value) and math.isfinite(value) and value == int(value)


def _is_number(value: JsValue, constraints: list[Any]) -> bool:
    if not is_number(value):
        return False
    options = _options(constraints) or {}
    if math.isinf(value):
        return bool(options.get("allowInfinity"))
    if math.isnan(value):
        return bool(options.get("allowNaN"))
    max_places = options.get("maxDecimalPlaces")
    if max_places is not None:
        places = 0
        if value % 1 != 0:
            places = len(number_to_string(value).split(".")[1])
        if places > max_places:
            return False
    return True


def _is_boolean(value: JsValue, _: list[Any]) -> bool:
    return isinstance(value, bool)


def _is_array(value: JsValue, _: list[Any]) -> bool:
    return isinstance(value, list)


def _is_object(value: JsValue, _: list[Any]) -> bool:
    return isinstance(value, dict | JsBuiltinFunction)


def _is_date(value: JsValue, _: list[Any]) -> bool:
    return isinstance(value, JsDate) and value.valid


def _enum_values(entity: Mapping[str, Any]) -> list[Any]:
    return list(entity.values())


def _includes(values: list[Any], value: JsValue) -> bool:
    if is_number(value) and math.isnan(value):
        return any(is_number(v) and math.isnan(v) for v in values)
    return any(strict_equals(v, value) for v in values)


def _is_enum(value: JsValue, constraints: list[Any]) -> bool:
    return _includes(_enum_values(constraints[0]), value)


def _is_in(value: JsValue, constraints: list[Any]) -> bool:
    possible = constraints[0]
    return isinstance(possible, list) and any(strict_equals(v, value) for v in possible)


def _min(value: JsValue, constraints: list[Any]) -> bool:
    bound = constraints[0]
    return is_number(value) and is_number(bound) and value >= bound


def _max(value: JsValue, constraints: list[Any]) -> bool:
    bound = constraints[0]
    return is_number(value) and is_number(bound) and value <= bound


def _length_options(constraints: list[Any], with_min: bool, with_max: bool) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if with_min:
        options["min"] = constraints[0]
    if with_max:
        options["max"] = constraints[1 if with_min else 0]
    return options


def _max_length(value: JsValue, constraints: list[Any]) -> bool:
    return isinstance(value, str) and validator_js.is_length(
        value, {"min": 0, "max": constraints[0]}
    )


def _min_length(value: JsValue, constraints: list[Any]) -> bool:
    return isinstance(value, str) and validator_js.is_length(value, {"min": constraints[0]})


def _is_length(value: JsValue, constraints: list[Any]) -> bool:
    return isinstance(value, str) and validator_js.is_length(
        value, {"min": constraints[0], "max": constraints[1]}
    )


def _is_length_message(value: JsValue, constraints: list[Any], each_prefix: str) -> str:
    low = constraints[0]
    high = constraints[1] if len(constraints) > 1 else None
    is_min = not is_nullish(low)
    is_max = not is_nullish(high)
    length = _js_length(value)
    if is_min and (not truthy(value) or (length is not None and length < low)):
        return each_prefix + "$property must be longer than or equal to $constraint1 characters"
    if is_max and length is not None and high is not None and length > high:
        return each_prefix + "$property must be shorter than or equal to $constraint2 characters"
    return (
        each_prefix
        + "$property must be longer than or equal to $constraint1 and shorter than or equal to "
        "$constraint2 characters"
    )


def _js_length(value: JsValue) -> int | None:
    if isinstance(value, str):
        return len(value.encode("utf-16-le")) // 2
    if isinstance(value, list):
        return len(value)
    return None


def _matches(value: JsValue, constraints: list[Any]) -> bool:
    if not isinstance(value, str):
        return False
    pattern = constraints[0]
    if isinstance(pattern, Mapping):
        regex = JsRegExp(pattern["regex"], pattern["flags"])
    else:
        modifiers = constraints[1] if len(constraints) > 1 else None
        regex = JsRegExp(str(pattern), modifiers or "")
    return regex.test(value)


def _is_uuid(value: JsValue, constraints: list[Any]) -> bool:
    if not isinstance(value, str):
        return False
    version = constraints[0] if constraints else None
    if isinstance(version, list):
        return any(validator_js.is_uuid(value, v) for v in version)
    return validator_js.is_uuid(value, version)


def _is_email(value: JsValue, constraints: list[Any]) -> bool:
    return isinstance(value, str) and validator_js.is_email(value, _options(constraints))


def _is_url(value: JsValue, constraints: list[Any]) -> bool:
    return isinstance(value, str) and validator_js.is_url(value, _options(constraints))


def _is_iso8601(value: JsValue, constraints: list[Any]) -> bool:
    return isinstance(value, str) and validator_js.is_iso8601(value, _options(constraints))


def _array_max_size(value: JsValue, constraints: list[Any]) -> bool:
    return isinstance(value, list) and len(value) <= constraints[0]


def _array_min_size(value: JsValue, constraints: list[Any]) -> bool:
    return isinstance(value, list) and len(value) >= constraints[0]


def _array_not_empty(value: JsValue, _: list[Any]) -> bool:
    return isinstance(value, list) and len(value) > 0


def _array_unique(value: JsValue, _: list[Any]) -> bool:
    if not isinstance(value, list):
        return False
    unique = [
        item
        for index, item in enumerate(value)
        if next(i for i, other in enumerate(value) if strict_equals(other, item)) == index
    ]
    return len(unique) == len(value)


def _is_region_code(value: JsValue, _: list[Any]) -> bool:
    if is_nullish(value):
        return True
    return isinstance(value, str) and len(value) == 12 and value.isascii() and value.isdigit()


MAX_METADATA_JSON_LENGTH = 2048


def _max_json_length(value: JsValue, _: list[Any]) -> bool:
    if value is UNDEFINED:
        raise TypeError("Cannot read properties of undefined (reading 'length')")
    return len(stringify(value).encode("utf-16-le")) // 2 <= MAX_METADATA_JSON_LENGTH


MD5_HASH_MESSAGE = "Password must be a valid MD5 hash (32 hexadecimal characters)"


def _md5_password(value: JsValue, _: list[Any]) -> bool:
    if not truthy(value) or not isinstance(value, str):
        return False
    return len(value) == 32 and all(c in "0123456789abcdefABCDEF" for c in value)


BUILTINS: dict[str, Constraint] = {
    "isDefined": Constraint(_is_defined),
    "isNotEmpty": Constraint(_is_not_empty),
    "isString": Constraint(_is_string),
    "isInt": Constraint(_is_int),
    "isNumber": Constraint(_is_number),
    "isBoolean": Constraint(_is_boolean),
    "isArray": Constraint(_is_array),
    "isObject": Constraint(_is_object),
    "isDate": Constraint(_is_date),
    "isEnum": Constraint(_is_enum),
    "isIn": Constraint(_is_in),
    "min": Constraint(_min),
    "max": Constraint(_max),
    "maxLength": Constraint(_max_length),
    "minLength": Constraint(_min_length),
    "isLength": Constraint(_is_length, _is_length_message),
    "matches": Constraint(_matches),
    "isUuid": Constraint(_is_uuid),
    "isEmail": Constraint(_is_email),
    "isUrl": Constraint(_is_url),
    "isIso8601": Constraint(_is_iso8601),
    "isDateString": Constraint(_is_iso8601),
    "arrayMaxSize": Constraint(_array_max_size),
    "arrayMinSize": Constraint(_array_min_size),
    "arrayNotEmpty": Constraint(_array_not_empty),
    "arrayUnique": Constraint(_array_unique),
    "isRegionCode": Constraint(_is_region_code),
    "maxJsonLength": Constraint(_max_json_length),
}

ANONYMOUS: dict[str, Constraint] = {
    MD5_HASH_MESSAGE: Constraint(_md5_password),
}


def resolve(key: str, default_message: str | None) -> Constraint | None:
    if key == "customValidation":
        return ANONYMOUS.get(default_message or "")
    return BUILTINS.get(key)
