"""validator.js 13.15.35 字符串校验函数的 Python 移植。

所有函数在入口把 ``str`` 换算为 UTF-16 码元序列（非 BMP 字符拆成代理对），
之后的长度、下标与正则匹配均作用于码元，与 JS 字符串语义一致。
options 中值为 ``None`` 的键等同于 JS 的 ``undefined``。
"""

import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import lru_cache
from typing import Any, cast

__all__ = [
    "is_byte_length",
    "is_email",
    "is_fqdn",
    "is_ip",
    "is_iso8601",
    "is_length",
    "is_url",
    "is_uuid",
]

_JS_WHITESPACE = (
    "\t\n\x0b\x0c\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_JS_LINE_TERMINATORS = "\n\r\u2028\u2029"
_ASTRAL = re.compile("[\U00010000-\U0010ffff]")

_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "f": "\f", "v": "\x0b"}
_CLASS_ESCAPES = {"d": "0-9", "w": "A-Za-z0-9_", "s": _JS_WHITESPACE}
_ATOM_ESCAPES = {
    "d": "[0-9]",
    "D": "[^0-9]",
    "w": "[A-Za-z0-9_]",
    "W": "[^A-Za-z0-9_]",
    "s": f"[{_JS_WHITESPACE}]",
    "S": f"[^{_JS_WHITESPACE}]",
}


def _literal(ch: str) -> str:
    return f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}"


def _count_groups(source: str) -> int:
    count = 0
    i = 0
    in_class = False
    while i < len(source):
        ch = source[i]
        if ch == "\\":
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif (
            ch == "("
            and (not source.startswith("?", i + 1) or source.startswith("?<", i + 1))
            and not source.startswith(("?<=", "?<!"), i + 1)
        ):
            count += 1
        i += 1
    return count


def _translate_escape(source: str, i: int, *, in_class: bool, groups: int) -> tuple[str, int]:
    ch = source[i + 1] if i + 1 < len(source) else "\\"
    end = i + 2
    if ch in _SIMPLE_ESCAPES:
        return _literal(_SIMPLE_ESCAPES[ch]), end
    if ch == "c":
        if end < len(source) and source[end].isascii() and source[end].isalpha():
            return _literal(chr(ord(source[end]) % 32)), end + 1
        return _literal("\\") + _literal("c"), end
    if ch == "x" and re.fullmatch("[0-9A-Fa-f]{2}", source[end : end + 2]):
        return _literal(chr(int(source[end : end + 2], 16))), end + 2
    if ch == "u" and re.fullmatch("[0-9A-Fa-f]{4}", source[end : end + 4]):
        return _literal(chr(int(source[end : end + 4], 16))), end + 4
    if in_class:
        if ch in _CLASS_ESCAPES:
            return _CLASS_ESCAPES[ch], end
        if ch == "b":
            return _literal("\b"), end
        if ch in "DW":
            return f"\\{ch}", end
        if ch == "S":
            raise ValueError("unsupported JS class escape \\S")
        return _octal_or_identity(source, i)
    if ch in _ATOM_ESCAPES:
        return _ATOM_ESCAPES[ch], end
    if ch in "bB":
        return f"\\{ch}", end
    if ch.isdigit() and ch != "0":
        digits = re.match("[0-9]+", source[i + 1 :])
        number = int(digits.group()) if digits else 0
        if digits and number <= groups:
            return f"(?({number})\\{number})", i + 1 + len(digits.group())
    return _octal_or_identity(source, i)


def _octal_or_identity(source: str, i: int) -> tuple[str, int]:
    ch = source[i + 1]
    if ch in "01234567":
        width = 3 if ch in "0123" else 2
        digits = re.match(f"[0-7]{{1,{width}}}", source[i + 1 :])
        text = digits.group() if digits else ch
        return _literal(chr(int(text, 8))), i + 1 + len(text)
    return _literal(ch), i + 2


@lru_cache(maxsize=256)
def _js_regex(source: str, flags: str = "") -> re.Pattern[str]:
    groups = _count_groups(source)
    out: list[str] = []
    i = 0
    in_class = False
    class_start = False
    while i < len(source):
        ch = source[i]
        if ch == "\\":
            piece, i = _translate_escape(source, i, in_class=in_class, groups=groups)
            out.append(piece)
            class_start = False
            continue
        if in_class:
            if ch == "]":
                if class_start and out[-1] == "[^":
                    out[-1] = r"[\x00-\U0010ffff]"
                elif class_start:
                    out[-1] = "(?!)"
                else:
                    out.append("]")
                in_class = False
            elif ch in "[&~|":
                out.append(_literal(ch))
            else:
                out.append(ch)
            class_start = False
            i += 1
            continue
        if ch == "[":
            in_class = True
            class_start = True
            if source.startswith("[^", i):
                out.append("[^")
                i += 2
            else:
                out.append("[")
                i += 1
            continue
        if ch == ".":
            out.append(f"[^{_JS_LINE_TERMINATORS}]")
        elif ch == "$":
            out.append(r"\Z")
        elif source.startswith("(?<", i) and not source.startswith(("(?<=", "(?<!"), i):
            out.append("(?P<")
            i += 3
            continue
        else:
            out.append(ch)
        i += 1
    py_flags = re.ASCII | (re.IGNORECASE if "i" in flags else 0)
    return re.compile("".join(out), py_flags)


def _to_js(value: str) -> str:
    if not _ASTRAL.search(value):
        return value
    units = value.encode("utf-16-le", "surrogatepass")
    return "".join(map(chr, memoryview(units).cast("H")))


def _from_js(value: str) -> str:
    return value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


def _assert_string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"Expected a string but received a {type(value).__name__}")
    return _to_js(value)


def _truthy(value: object) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)):
        return value != 0 and not math.isnan(value)
    if isinstance(value, str):
        return value != ""
    return True


def _js_to_string(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    return str(value)


def _merge(options: object, defaults: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if isinstance(options, Mapping):
        merged = {k: v for k, v in options.items() if v is not None}
    for key, default in defaults.items():
        merged.setdefault(key, default)
    return merged


def _check_host(host: str, matches: Sequence[Any]) -> bool:
    for match in matches:
        if isinstance(match, str) and host == _to_js(match):
            return True
        if isinstance(match, re.Pattern) and match.search(_from_js(host)):
            return True
    return False


def _js_lower(value: str) -> str:
    return _to_js(_from_js(value).lower())


def _utf8_length(value: str) -> int:
    try:
        return len(_from_js(value).encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("URI malformed") from None


def is_byte_length(value: str, options: Mapping[str, Any] | None = None) -> bool:
    """``isByteLength``；``options`` 为 ``None`` 时等同 JS 传入 ``undefined``。"""
    s = _assert_string(value)
    min_: Any
    max_: Any
    if isinstance(options, Mapping):
        raw_min = options.get("min")
        min_ = raw_min if _truthy(raw_min) else 0
        max_ = options.get("max")
    else:
        min_ = None
        max_ = None
    length = _utf8_length(s)
    if min_ is None:
        return False
    return bool(length >= min_ and (max_ is None or length <= max_))


_PRESENTATION_SEQUENCES = _js_regex(r"[^\uFE0F\uFE0E][\uFE0F\uFE0E]")
_SURROGATE_PAIRS = _js_regex(r"[\uD800-\uDBFF][\uDC00-\uDFFF]")


def is_length(value: str, options: Mapping[str, Any] | None = None) -> bool:
    s = _assert_string(value)
    min_: Any = 0
    max_: Any = None
    if isinstance(options, Mapping):
        raw_min = options.get("min")
        min_ = raw_min if _truthy(raw_min) else 0
        max_ = options.get("max")
    presentation_sequences = len(_PRESENTATION_SEQUENCES.findall(s))
    surrogate_pairs = len(_SURROGATE_PAIRS.findall(s))
    length = len(s) - presentation_sequences - surrogate_pairs
    is_inside_range = bool(length >= cast("float", min_)) and (max_ is None or length <= max_)
    discrete = options.get("discreteLengths") if isinstance(options, Mapping) else None
    if is_inside_range and isinstance(discrete, (list, tuple)):
        return any(
            isinstance(d, (int, float)) and not isinstance(d, bool) and d == length
            for d in discrete
        )
    return is_inside_range


_DEFAULT_FQDN_OPTIONS: Mapping[str, Any] = {
    "require_tld": True,
    "allow_underscores": False,
    "allow_trailing_dot": False,
    "allow_numeric_tld": False,
    "allow_wildcard": False,
    "ignore_max_length": False,
}
_FQDN_TLD = _js_regex(
    r"^([a-z\u00A1-\u00A8\u00AA-\uD7FF\uF900-\uFDCF\uFDF0-\uFFEF]{2,}|xn[a-z0-9-]{2,})$", "i"
)
_FQDN_WHITESPACE = _js_regex(r"\s")
_FQDN_NUMERIC = _js_regex(r"^\d+$")
_FQDN_PART = _js_regex(r"^[a-z_\u00a1-\uffff0-9-]+$", "i")
_FQDN_FULL_WIDTH = _js_regex(r"[\uff01-\uff5e]")
_FQDN_HYPHEN_EDGE = _js_regex(r"^-|-$")


def is_fqdn(value: str, options: Mapping[str, Any] | None = None) -> bool:
    s = _assert_string(value)
    opts = _merge(options, _DEFAULT_FQDN_OPTIONS)

    if _truthy(opts["allow_trailing_dot"]) and s[-1:] == ".":
        s = s[:-1]

    if opts["allow_wildcard"] is True and s.startswith("*."):
        s = s[2:]
    parts = s.split(".")
    tld = parts[-1]
    if _truthy(opts["require_tld"]):
        if len(parts) < 2:
            return False
        if not _truthy(opts["allow_numeric_tld"]) and not _FQDN_TLD.search(tld):
            return False

        if _FQDN_WHITESPACE.search(tld):
            return False

    if not _truthy(opts["allow_numeric_tld"]) and _FQDN_NUMERIC.search(tld):
        return False

    def check_part(part: str) -> bool:
        if len(part) > 63 and not _truthy(opts["ignore_max_length"]):
            return False
        if not _FQDN_PART.search(part):
            return False
        if _FQDN_FULL_WIDTH.search(part):
            return False
        if _FQDN_HYPHEN_EDGE.search(part):
            return False
        return _truthy(opts["allow_underscores"]) or "_" not in part

    return all(check_part(part) for part in parts)


_IPV4_SEGMENT_FORMAT = "(?:[0-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5])"
_IPV4_ADDRESS_FORMAT = f"({_IPV4_SEGMENT_FORMAT}[.]){{3}}{_IPV4_SEGMENT_FORMAT}"
_IPV4_ADDRESS_REGEXP = _js_regex(f"^{_IPV4_ADDRESS_FORMAT}$")
_IPV6_SEGMENT_FORMAT = "(?:[0-9a-fA-F]{1,4})"
_V4 = _IPV4_ADDRESS_FORMAT
_V6 = _IPV6_SEGMENT_FORMAT
_IPV6_ADDRESS_REGEXP = _js_regex(
    "^("
    f"(?:{_V6}:){{7}}(?:{_V6}|:)|"
    f"(?:{_V6}:){{6}}(?:{_V4}|:{_V6}|:)|"
    f"(?:{_V6}:){{5}}(?::{_V4}|(:{_V6}){{1,2}}|:)|"
    f"(?:{_V6}:){{4}}(?:(:{_V6}){{0,1}}:{_V4}|(:{_V6}){{1,3}}|:)|"
    f"(?:{_V6}:){{3}}(?:(:{_V6}){{0,2}}:{_V4}|(:{_V6}){{1,4}}|:)|"
    f"(?:{_V6}:){{2}}(?:(:{_V6}){{0,3}}:{_V4}|(:{_V6}){{1,5}}|:)|"
    f"(?:{_V6}:){{1}}(?:(:{_V6}){{0,4}}:{_V4}|(:{_V6}){{1,6}}|:)|"
    f"(?::((?::{_V6}){{0,5}}:{_V4}|(?::{_V6}){{1,7}}|:))"
    ")(%[0-9a-zA-Z.]{1,})?$"
)


def is_ip(value: str, options: Mapping[str, Any] | str | int | None = None) -> bool:
    """``isIP``；``options`` 可为 ``{"version": ...}`` 或直接传版本号。"""
    s = _assert_string(value)
    raw = options.get("version") if isinstance(options, Mapping) else options
    if not _truthy(raw):
        return is_ip(s, {"version": 4}) or is_ip(s, {"version": 6})
    version = _js_to_string(raw)
    if version == "4":
        return bool(_IPV4_ADDRESS_REGEXP.search(s))
    if version == "6":
        return bool(_IPV6_ADDRESS_REGEXP.search(s))
    return False


_DEFAULT_EMAIL_OPTIONS: Mapping[str, Any] = {
    "allow_display_name": False,
    "allow_underscores": False,
    "require_display_name": False,
    "allow_utf8_local_part": True,
    "require_tld": True,
    "blacklisted_chars": "",
    "ignore_max_length": False,
    "host_blacklist": [],
    "host_whitelist": [],
}

_SPLIT_NAME_ADDRESS = _js_regex(r"^([^\x00-\x1F\x7F-\x9F\cX]+)<", "i")
_EMAIL_USER_PART = _js_regex(r"^[a-z\d!#\$%&'\*\+\-\/=\?\^_`{\|}~]+$", "i")
_GMAIL_USER_PART = _js_regex(r"^[a-z\d]+$")
_QUOTED_EMAIL_USER = _js_regex(
    r"^([\s\x01-\x08\x0b\x0c\x0e-\x1f\x7f\x21\x23-\x5b\x5d-\x7e]|(\\[\x01-\x09\x0b\x0c\x0d-\x7f]))*$",
    "i",
)
_EMAIL_USER_UTF8_PART = _js_regex(
    r"^[a-z\d!#\$%&'\*\+\-\/=\?\^_`{\|}~\u00A1-\uD7FF\uF900-\uFDCF\uFDF0-\uFFEF]+$", "i"
)
_QUOTED_EMAIL_USER_UTF8 = _js_regex(
    r"^([\s\x01-\x08\x0b\x0c\x0e-\x1f\x7f\x21\x23-\x5b\x5d-\x7e\u00A0-\uD7FF\uF900-\uFDCF\uFDF0-\uFFEF]"
    r"|(\\[\x01-\x09\x0b\x0c\x0d-\x7f\u00A0-\uD7FF\uF900-\uFDCF\uFDF0-\uFFEF]))*$",
    "i",
)
_DEFAULT_MAX_EMAIL_LENGTH = 254
_DISPLAY_NAME_QUOTES = _js_regex(r'^"(.+)"$')
_DISPLAY_NAME_ILLEGAL = _js_regex(r'[\.";<>]')
_ANGLE_BRACKETS = _js_regex(r"(^<|>$)")
_DOTS = _js_regex(r"\.")


def _validate_display_name(display_name: str) -> bool:
    without_quotes = _DISPLAY_NAME_QUOTES.sub(r"\1", display_name, count=1)
    if not without_quotes.strip(_JS_WHITESPACE):
        return False

    if _DISPLAY_NAME_ILLEGAL.search(without_quotes):
        if without_quotes == display_name:
            return False

        if without_quotes.count('"') != without_quotes.count('\\"'):
            return False
    return True


def is_email(value: str, options: Mapping[str, Any] | None = None) -> bool:
    s = _assert_string(value)
    opts = _merge(options, _DEFAULT_EMAIL_OPTIONS)

    if _truthy(opts["require_display_name"]) or _truthy(opts["allow_display_name"]):
        display_email = _SPLIT_NAME_ADDRESS.search(s)
        if display_email:
            display_name = display_email.group(1)
            s = _ANGLE_BRACKETS.sub("", s.replace(display_name, "", 1))
            if display_name.endswith(" "):
                display_name = display_name[:-1]
            if not _validate_display_name(display_name):
                return False
        elif _truthy(opts["require_display_name"]):
            return False

    if not _truthy(opts["ignore_max_length"]) and len(s) > _DEFAULT_MAX_EMAIL_LENGTH:
        return False

    user, _, domain = s.rpartition("@")
    lower_domain = _js_lower(domain)

    if len(opts["host_blacklist"]) > 0 and _check_host(lower_domain, opts["host_blacklist"]):
        return False

    if len(opts["host_whitelist"]) > 0 and not _check_host(lower_domain, opts["host_whitelist"]):
        return False

    if _truthy(opts.get("domain_specific_validation")) and lower_domain in (
        "gmail.com",
        "googlemail.com",
    ):
        user = _js_lower(user)
        username = user.split("+")[0]
        if not is_byte_length(_DOTS.sub("", username), {"min": 6, "max": 30}):
            return False
        if not all(_GMAIL_USER_PART.search(part) for part in username.split(".")):
            return False

    if opts["ignore_max_length"] is False and (
        not is_byte_length(user, {"max": 64}) or not is_byte_length(domain, {"max": 254})
    ):
        return False

    fqdn_options = {
        "require_tld": opts["require_tld"],
        "ignore_max_length": opts["ignore_max_length"],
        "allow_underscores": opts["allow_underscores"],
    }
    if not is_fqdn(domain, fqdn_options):
        if not _truthy(opts.get("allow_ip_domain")):
            return False
        if not is_ip(domain):
            if not domain.startswith("[") or not domain.endswith("]"):
                return False
            no_bracket_domain = domain[1:-1]
            if len(no_bracket_domain) == 0 or not is_ip(no_bracket_domain):
                return False

    blacklisted_chars = opts["blacklisted_chars"]
    if _truthy(blacklisted_chars) and _js_regex(f"[{blacklisted_chars}]+").search(user):
        return False

    if user[:1] == '"' and user[-1:] == '"':
        user = user[1:-1]
        quoted = (
            _QUOTED_EMAIL_USER_UTF8
            if _truthy(opts["allow_utf8_local_part"])
            else _QUOTED_EMAIL_USER
        )
        return bool(quoted.search(user))
    pattern = _EMAIL_USER_UTF8_PART if _truthy(opts["allow_utf8_local_part"]) else _EMAIL_USER_PART
    return all(pattern.search(part) for part in user.split("."))


_DEFAULT_URL_OPTIONS: Mapping[str, Any] = {
    "protocols": ["http", "https", "ftp"],
    "require_tld": True,
    "require_protocol": False,
    "require_host": True,
    "require_port": False,
    "require_valid_protocol": True,
    "allow_underscores": False,
    "allow_trailing_dot": False,
    "allow_protocol_relative_urls": False,
    "allow_fragments": True,
    "allow_query_components": True,
    "validate_length": True,
    "max_allowed_length": 2084,
}
_WRAPPED_IPV6 = _js_regex(r"^\[([^\]]+)\](?::([0-9]+))?$")
_URL_FORBIDDEN = _js_regex(r"[\s<>]")
_URL_PROTOCOL = _js_regex(r"^([a-z][a-z0-9+\-.]*):", "i")
_URL_VALID_AUTH = _js_regex(r"^[a-zA-Z0-9\-_.%:]*$")
_URL_ENCODED_CONTENT = _js_regex(r"%[0-9a-fA-F]{2}")
_URL_LOOKS_LIKE_PORT = _js_regex(r"^[0-9]")
_URL_PORT = _js_regex(r"^[0-9]+$")


def _parse_port(port_str: str) -> float:
    digits = port_str.lstrip("0") or "0"
    return math.inf if len(digits) > 6 else int(digits)


def is_url(value: str, options: Mapping[str, Any] | None = None) -> bool:
    url = _assert_string(value)
    if not url or _URL_FORBIDDEN.search(url):
        return False
    if url.startswith("mailto:"):
        return False
    opts = _merge(options, _DEFAULT_URL_OPTIONS)
    if _truthy(opts["validate_length"]) and len(url) > opts["max_allowed_length"]:
        return False
    if not _truthy(opts["allow_fragments"]) and "#" in url:
        return False
    if not _truthy(opts["allow_query_components"]) and ("?" in url or "&" in url):
        return False

    url = url.split("#")[0]
    url = url.split("?")[0]

    protocol_match = _URL_PROTOCOL.search(url)
    had_explicit_protocol = False

    def clean_up_protocol(potential_protocol: str, prefix_length: int) -> str | None:
        nonlocal had_explicit_protocol
        had_explicit_protocol = True
        protocol = _js_lower(potential_protocol)
        if _truthy(opts["require_valid_protocol"]) and protocol not in opts["protocols"]:
            return None
        return url[prefix_length:]

    if protocol_match:
        prefix_length = len(protocol_match.group(0))
        potential_protocol = protocol_match.group(1)
        after_colon = url[prefix_length:]
        cleaned: str | None = url

        starts_with_slashes = after_colon[:2] == "//"
        if not starts_with_slashes:
            first_slash_position = after_colon.find("/")
            before_slash = (
                after_colon if first_slash_position == -1 else after_colon[:first_slash_position]
            )
            at_position = before_slash.find("@")
            if at_position != -1:
                before_at = before_slash[:at_position]
                is_valid_auth = bool(_URL_VALID_AUTH.search(before_at))
                has_encoded_content = bool(_URL_ENCODED_CONTENT.search(before_at))
                if is_valid_auth and not has_encoded_content:
                    if _truthy(opts["require_protocol"]):
                        return False
                else:
                    cleaned = clean_up_protocol(potential_protocol, prefix_length)
            elif _URL_LOOKS_LIKE_PORT.search(after_colon):
                if _truthy(opts["require_protocol"]):
                    return False
            else:
                cleaned = clean_up_protocol(potential_protocol, prefix_length)
        else:
            cleaned = clean_up_protocol(potential_protocol, prefix_length)
        if cleaned is None:
            return False
        url = cleaned
    elif _truthy(opts["require_protocol"]):
        return False

    if url[:2] == "//":
        if not had_explicit_protocol and not _truthy(opts["allow_protocol_relative_urls"]):
            return False
        url = url[2:]
    if url == "":
        return False
    url = url.split("/")[0]
    if url == "" and not _truthy(opts["require_host"]):
        return True

    split = url.split("@")
    if len(split) > 1:
        if _truthy(opts.get("disallow_auth")):
            return False
        if split[0] == "":
            return False
        auth = split.pop(0)
        auth_parts = auth.split(":")
        if ":" in auth and len(auth_parts) > 2:
            return False
        user = auth_parts[0]
        password = auth_parts[1] if len(auth_parts) > 1 else None
        if user == "" and password == "":
            return False
    hostname = "@".join(split)

    port_str: str | None = None
    ipv6: str | None = None
    ipv6_match = _WRAPPED_IPV6.search(hostname)
    if ipv6_match:
        host = ""
        ipv6 = ipv6_match.group(1)
        port_str = ipv6_match.group(2) or None
    else:
        host_parts = hostname.split(":")
        host = host_parts.pop(0)
        if host_parts:
            port_str = ":".join(host_parts)

    if port_str is not None and len(port_str) > 0:
        if not _URL_PORT.search(port_str):
            return False
        port = _parse_port(port_str)
        if port <= 0 or port > 65535:
            return False
    elif _truthy(opts["require_port"]):
        return False

    if _truthy(opts.get("host_whitelist")):
        return _check_host(host, opts["host_whitelist"])

    if host == "" and not _truthy(opts["require_host"]):
        return True

    if not is_ip(host) and not is_fqdn(host, opts) and (not ipv6 or not is_ip(ipv6, 6)):
        return False

    host = host or ipv6 or ""

    return not (_truthy(opts.get("host_blacklist")) and _check_host(host, opts["host_blacklist"]))


_UUID_PATTERNS: Mapping[str, re.Pattern[str]] = {
    "1": _js_regex(r"^[0-9A-F]{8}-[0-9A-F]{4}-1[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}$", "i"),
    "2": _js_regex(r"^[0-9A-F]{8}-[0-9A-F]{4}-2[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}$", "i"),
    "3": _js_regex(r"^[0-9A-F]{8}-[0-9A-F]{4}-3[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}$", "i"),
    "4": _js_regex(r"^[0-9A-F]{8}-[0-9A-F]{4}-4[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}$", "i"),
    "5": _js_regex(r"^[0-9A-F]{8}-[0-9A-F]{4}-5[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}$", "i"),
    "6": _js_regex(r"^[0-9A-F]{8}-[0-9A-F]{4}-6[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}$", "i"),
    "7": _js_regex(r"^[0-9A-F]{8}-[0-9A-F]{4}-7[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}$", "i"),
    "8": _js_regex(r"^[0-9A-F]{8}-[0-9A-F]{4}-8[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}$", "i"),
    "nil": _js_regex(r"^00000000-0000-0000-0000-000000000000$", "i"),
    "max": _js_regex(r"^ffffffff-ffff-ffff-ffff-ffffffffffff$", "i"),
    "loose": _js_regex(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$", "i"),
    "all": _js_regex(
        r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
        r"|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$",
        "i",
    ),
}
_OBJECT_PROTOTYPE_KEYS = frozenset(
    {
        "__proto__",
        "__defineGetter__",
        "__defineSetter__",
        "__lookupGetter__",
        "__lookupSetter__",
        "constructor",
        "hasOwnProperty",
        "isPrototypeOf",
        "propertyIsEnumerable",
        "toLocaleString",
        "toString",
        "valueOf",
    }
)


def is_uuid(value: str, version: str | int | None = None) -> bool:
    """``isUUID``；version 为 Object.prototype 上的属性名时与 JS 一样抛 TypeError。"""
    s = _assert_string(value)
    key = "all" if version is None else _js_to_string(version)
    if key in _OBJECT_PROTOTYPE_KEYS:
        raise TypeError(f"uuid[{key!r}].test is not a function")
    pattern = _UUID_PATTERNS.get(key)
    return bool(pattern and pattern.search(s))


_ISO8601 = _js_regex(
    r"^([\+-]?\d{4}(?!\d{2}\b))((-?)((0[1-9]|1[0-2])(\3([12]\d|0[1-9]|3[01]))?|W([0-4]\d|5[0-3])(-?[1-7])?|(00[1-9]|0[1-9]\d|[12]\d{2}|3([0-5]\d|6[1-6])))([T\s]((([01]\d|2[0-3])((:?)[0-5]\d)?|24:?00)([\.,]\d+(?!:))?)?(\17[0-5]\d([\.,]\d+)?)?([zZ]|([\+-])([01]\d|2[0-3]):?([0-5]\d)?)?)?)?$"
)
_ISO8601_STRICT_SEPARATOR = _js_regex(
    r"^([\+-]?\d{4}(?!\d{2}\b))((-?)((0[1-9]|1[0-2])(\3([12]\d|0[1-9]|3[01]))?|W([0-4]\d|5[0-3])(-?[1-7])?|(00[1-9]|0[1-9]\d|[12]\d{2}|3([0-5]\d|6[1-6])))([T]((([01]\d|2[0-3])((:?)[0-5]\d)?|24:?00)([\.,]\d+(?!:))?)?(\17[0-5]\d([\.,]\d+)?)?([zZ]|([\+-])([01]\d|2[0-3]):?([0-5]\d)?)?)?)?$"
)
_ORDINAL_DATE = _js_regex(r"^(\d{4})-?(\d{3})([ T]{1}\.*|$)")
_DATE_PARTS = _js_regex(r"(\d{4})-?(\d{0,2})-?(\d*)")
_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _is_leap_year(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


def _local_offset_is_positive(year: int, month: int, day: int) -> bool:
    offset = datetime(year, month, day).astimezone().utcoffset()
    return offset is not None and offset.total_seconds() > 0


def _js_date_matches(year: int, month: int, day: int) -> bool:
    """``new Date("Y-MM-DD")`` 的 UTC 年月日是否等于 (year, month, day)，按 V8 解析规则。"""
    if not 1 <= month <= 12:
        return False
    days = 29 if month == 2 and _is_leap_year(year) else _DAYS_IN_MONTH[month - 1]
    if not 1 <= day <= days or year < 100:
        return False
    return year >= 1000 or not _local_offset_is_positive(year, month, day)


def _is_valid_date(s: str) -> bool:
    ordinal_match = _ORDINAL_DATE.search(s)
    if ordinal_match:
        o_year = int(ordinal_match.group(1))
        o_day = int(ordinal_match.group(2))
        if _is_leap_year(o_year):
            return o_day <= 366
        return o_day <= 365
    match = _DATE_PARTS.search(s)
    if match is None:
        return True
    year = int(match.group(1))
    month = int(match.group(2) or "0")
    day_digits = match.group(3).lstrip("0")
    if month and day_digits:
        return len(day_digits) <= 2 and _js_date_matches(year, month, int(day_digits))
    return True


def is_iso8601(value: str, options: Mapping[str, Any] | None = None) -> bool:
    s = _assert_string(value)
    opts: Mapping[str, Any] = options if options is not None else {}
    pattern = _ISO8601_STRICT_SEPARATOR if _truthy(opts.get("strictSeparator")) else _ISO8601
    check = bool(pattern.search(s))
    if check and _truthy(opts.get("strict")):
        return _is_valid_date(s)
    return check
