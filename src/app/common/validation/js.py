"""JavaScript 值语义：undefined、类型转换、字符串化与正则，供 class-validator 行为复刻使用。"""

import json
import math
import re
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any, Final

type JsValue = Any


class Undefined:
    _instance: Undefined | None = None

    def __new__(cls) -> Undefined:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "undefined"

    def __bool__(self) -> bool:
        return False


UNDEFINED: Final = Undefined()

JS_WHITESPACE: Final = "".join(
    map(
        chr,
        [
            0x09,
            0x0A,
            0x0B,
            0x0C,
            0x0D,
            0x20,
            0xA0,
            0x1680,
            *range(0x2000, 0x200B),
            0x2028,
            0x2029,
            0x202F,
            0x205F,
            0x3000,
            0xFEFF,
        ],
    )
)
_WS_CLASS: Final = "".join(f"\\u{ord(c):04x}" for c in JS_WHITESPACE)
_DECIMAL_RE: Final = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", re.ASCII)
_RADIX_RE: Final = re.compile(r"0([xXoObB])([0-9a-fA-F]+)", re.ASCII)
_RADIX: Final = {"x": 16, "o": 8, "b": 2}
MAX_SAFE_INTEGER: Final = 2**53 - 1


def is_undefined(value: JsValue) -> bool:
    return value is UNDEFINED


def is_nullish(value: JsValue) -> bool:
    return value is None or value is UNDEFINED


def is_number(value: JsValue) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def is_object(value: JsValue) -> bool:
    return isinstance(value, dict | list)


def typeof(value: JsValue) -> str:
    if value is UNDEFINED:
        return "undefined"
    if isinstance(value, bool):
        return "boolean"
    if is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if callable(value) or isinstance(value, JsBuiltinFunction):
        return "function"
    return "object"


def truthy(value: JsValue) -> bool:
    if value is UNDEFINED or value is None or value is False:
        return False
    if is_number(value):
        return value != 0 and not math.isnan(value)
    if isinstance(value, str):
        return value != ""
    return True


def normalize_number(value: float) -> int | float:
    if math.isfinite(value) and value == int(value) and abs(value) <= MAX_SAFE_INTEGER:
        return int(value)
    return value


def trim(value: str) -> str:
    return value.strip(JS_WHITESPACE)


def to_number(value: JsValue) -> int | float:
    if value is UNDEFINED:
        return math.nan
    if value is None or value is False:
        return 0
    if value is True:
        return 1
    if is_number(value):
        return value  # type: ignore[no-any-return]
    if isinstance(value, str):
        return _string_to_number(value)
    if isinstance(value, list):
        return _string_to_number(to_string(value))
    return math.nan


def _string_to_number(text: str) -> int | float:
    text = trim(text)
    if text == "":
        return 0
    if text in {"Infinity", "+Infinity"}:
        return math.inf
    if text == "-Infinity":
        return -math.inf
    radix = _RADIX_RE.fullmatch(text)
    if radix:
        try:
            return normalize_number(float(int(radix.group(2), _RADIX[radix.group(1).lower()])))
        except ValueError:
            return math.nan
    if _DECIMAL_RE.fullmatch(text):
        return normalize_number(float(text))
    return math.nan


def number_to_string(value: float) -> str:
    """ECMAScript Number::toString(10)。"""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "0"
    if isinstance(value, int) and abs(value) > MAX_SAFE_INTEGER:
        value = float(value)
    sign = "-" if value < 0 else ""
    text = str(abs(value)) if isinstance(value, int) else repr(abs(value))
    parts = Decimal(text).normalize().as_tuple()
    digits = "".join(map(str, parts.digits))
    k = len(digits)
    n = int(parts.exponent) + k
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    exponent = n - 1
    exp_text = f"e{'+' if exponent >= 0 else '-'}{abs(exponent)}"
    mantissa = digits if k == 1 else f"{digits[0]}.{digits[1:]}"
    return sign + mantissa + exp_text


def to_string(value: JsValue) -> str:
    if value is UNDEFINED:
        return "undefined"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if is_number(value):
        return number_to_string(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ",".join("" if is_nullish(item) else to_string(item) for item in value)
    if isinstance(value, JsRegExp):
        return f"/{value.source}/{value.flags}"
    return "[object Object]"


def strict_equals(left: JsValue, right: JsValue) -> bool:
    if is_number(left) and is_number(right):
        return bool(left == right)
    if is_object(left) or is_object(right):
        return left is right
    return type(left) is type(right) and bool(left == right)


def stringify(value: JsValue) -> str:
    """JSON.stringify：undefined 属性省略，NaN / Infinity 输出 null。"""
    return json.dumps(_to_json(value), ensure_ascii=False, separators=(",", ":"))


def _to_json(value: JsValue) -> Any:
    if isinstance(value, dict):
        return {k: _to_json(v) for k, v in value.items() if v is not UNDEFINED}
    if isinstance(value, list):
        return [None if item is UNDEFINED else _to_json(item) for item in value]
    if isinstance(value, JsDate):
        return value.to_json()
    if is_number(value) and not math.isfinite(value):
        return None
    if is_number(value):
        return normalize_number(value)
    return value


def parse_json(text: str) -> JsValue:
    """JSON.parse；语法错误抛 ValueError。"""

    def reject_constant(name: str) -> Any:
        raise ValueError(name)

    return json.loads(
        text,
        parse_constant=reject_constant,
        parse_int=_parse_int,
        parse_float=lambda text: normalize_number(float(text)),
        object_pairs_hook=_last_key_wins,
    )


def _parse_int(text: str) -> int | float:
    number = int(text)
    return number if abs(number) <= MAX_SAFE_INTEGER else float(number)


def _last_key_wins(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        result.pop(key, None)
        result[key] = item
    return result


class JsRegExp:
    """JS RegExp 的 Python 等价物，`test` 采用 search 语义。"""

    def __init__(self, source: str, flags: str = "") -> None:
        self.source = source
        self.flags = flags
        self.pattern = re.compile(_translate(source, "s" in flags, "m" in flags), _re_flags(flags))

    def test(self, value: str) -> bool:
        return self.pattern.search(value) is not None


def _re_flags(flags: str) -> re.RegexFlag:
    result = re.RegexFlag(0)
    if "i" in flags:
        result |= re.IGNORECASE
    if "m" in flags:
        result |= re.MULTILINE
    return result


_CLASS_ESCAPES: Final[Mapping[str, str]] = {
    "d": "0-9",
    "w": "A-Za-z0-9_",
    "s": _WS_CLASS,
}
_NEGATED: Final[Mapping[str, str]] = {"D": "d", "W": "w", "S": "s"}


def _translate(source: str, dot_all: bool, multiline: bool) -> str:
    out: list[str] = []
    in_class = False
    i = 0
    while i < len(source):
        char = source[i]
        if char == "\\" and i + 1 < len(source):
            nxt = source[i + 1]
            if nxt in _CLASS_ESCAPES:
                body = _CLASS_ESCAPES[nxt]
                out.append(body if in_class else f"[{body}]")
            elif nxt in _NEGATED:
                body = _CLASS_ESCAPES[_NEGATED[nxt]]
                if in_class:
                    raise ValueError(f"unsupported negated class escape in set: {source}")
                out.append(f"[^{body}]")
            else:
                out.append(char + nxt)
            i += 2
            continue
        if in_class:
            if char == "]":
                in_class = False
            out.append(char)
        elif char == "[":
            in_class = True
            out.append(char)
            if source.startswith("^", i + 1):
                out.append("^")
                i += 1
            if source.startswith("]", i + 1):
                out.append("\\]")
                i += 1
        elif char == "$":
            out.append("(?=[\\n\\r\\u2028\\u2029]|\\Z)" if multiline else "\\Z")
        elif char == "." and not dot_all:
            out.append("[^\\n\\r\\u2028\\u2029]")
        else:
            out.append(char)
        i += 1
    return "".join(out)


def js_replace(text: str, pattern: str, replacement: str) -> str:
    """`text.replace(new RegExp(pattern, 'g'), replacement)`，pattern 按字面量匹配。"""
    compiled = re.compile(re.escape(pattern))

    def substitute(match: re.Match[str]) -> str:
        return _substitution(
            replacement, match.group(0), text[: match.start()], text[match.end() :]
        )

    return compiled.sub(substitute, text)


def _substitution(replacement: str, matched: str, before: str, after: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(replacement):
        char = replacement[i]
        if char == "$" and i + 1 < len(replacement):
            nxt = replacement[i + 1]
            expansions: dict[str, str] = {"$": "$", "&": matched, "`": before, "'": after}
            if nxt in expansions:
                out.append(expansions[nxt])
                i += 2
                continue
        out.append(char)
        i += 1
    return "".join(out)


type JsFunction = Callable[..., JsValue]


class JsBuiltinFunction:
    """内置函数值（如 Array.prototype.sort），只参与类型判断与真值判断。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"function {self.name}() {{ [native code] }}"


ARRAY_PROTOTYPE_METHODS: Final = frozenset(
    {
        "at",
        "concat",
        "copyWithin",
        "entries",
        "every",
        "fill",
        "filter",
        "find",
        "findIndex",
        "findLast",
        "findLastIndex",
        "flat",
        "flatMap",
        "forEach",
        "includes",
        "indexOf",
        "join",
        "keys",
        "lastIndexOf",
        "map",
        "pop",
        "push",
        "reduce",
        "reduceRight",
        "reverse",
        "shift",
        "slice",
        "some",
        "sort",
        "splice",
        "toLocaleString",
        "toReversed",
        "toSorted",
        "toSpliced",
        "toString",
        "unshift",
        "values",
        "with",
        "constructor",
        "hasOwnProperty",
        "isPrototypeOf",
        "propertyIsEnumerable",
        "valueOf",
    }
)


_ISO_DATE_RE: Final = re.compile(
    r"(?P<year>[+-]\d{6}|\d{4})(?:-(?P<month>\d{2})(?:-(?P<day>\d{2}))?)?"
    r"(?:T(?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2})(?:\.(?P<fraction>\d+))?)?"
    r"(?P<offset>Z|[+-]\d{2}:\d{2})?)?",
    re.ASCII,
)
_MS_PER_DAY: Final = 86_400_000
_MAX_TIME: Final = 8.64e15


def _days_from_civil(year: int, month: int, day: int) -> int:
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    y = year - (month <= 2)
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _parse_iso_date(text: str) -> float:
    match = _ISO_DATE_RE.fullmatch(text)
    if not match or match.group("year") == "-000000":
        return math.nan
    parts = match.groupdict()
    year = int(parts["year"])
    month = int(parts["month"] or 1)
    day = int(parts["day"] or 1)
    hour = int(parts["hour"] or 0)
    minute = int(parts["minute"] or 0)
    second = int(parts["second"] or 0)
    millis = int((parts["fraction"] or "0")[:3].ljust(3, "0"))
    if not (1 <= month <= 12 and 1 <= day <= 31 and minute < 60 and second < 60):
        return math.nan
    if hour > 24 or (hour == 24 and (minute or second or millis)):
        return math.nan
    time = _days_from_civil(year, month, day) * _MS_PER_DAY
    time += ((hour * 60 + minute) * 60 + second) * 1000 + millis
    offset = parts["offset"]
    if offset and offset != "Z":
        sign = 1 if offset[0] == "+" else -1
        time -= sign * (int(offset[1:3]) * 60 + int(offset[4:6])) * 60_000
    return float(time) if abs(time) <= _MAX_TIME else math.nan


class JsDate:
    """JS Date：只实现 ES 规范的 ISO 日期字符串与数字时间戳，按 UTC 解释无偏移的时间。"""

    time: float

    def __init__(self, value: JsValue) -> None:
        if isinstance(value, JsDate):
            self.time = value.time
            return
        if isinstance(value, str):
            self.time = _parse_iso_date(value)
            return
        number = to_number(value)
        self.time = float(math.trunc(number)) if abs(number) <= _MAX_TIME else math.nan

    @property
    def valid(self) -> bool:
        return not math.isnan(self.time)

    def to_json(self) -> str | None:
        if not self.valid:
            return None
        days, millis = divmod(int(self.time), _MS_PER_DAY)
        z = days + 719468
        era = z // 146097
        doe = z - era * 146097
        yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
        doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
        mp = (5 * doy + 2) // 153
        day = doy - (153 * mp + 2) // 5 + 1
        month = mp + 3 if mp < 10 else mp - 9
        year = yoe + era * 400 + (month <= 2)
        seconds, ms = divmod(millis, 1000)
        minutes, sec = divmod(seconds, 60)
        hour, minute = divmod(minutes, 60)
        year_text = (
            f"{year:04d}" if 0 <= year <= 9999 else f"{'+' if year > 0 else '-'}{abs(year):06d}"
        )
        return f"{year_text}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{sec:02d}.{ms:03d}Z"
