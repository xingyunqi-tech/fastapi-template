from typing import TYPE_CHECKING, Any

import pytest

from app.common.validation.validator_js import (
    is_byte_length,
    is_email,
    is_fqdn,
    is_ip,
    is_iso8601,
    is_length,
    is_url,
    is_uuid,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

pytestmark = pytest.mark.unit

FUNCTIONS: Mapping[str, Callable[[str, Any], bool]] = {
    "is_email": is_email,
    "is_url": is_url,
    "is_uuid": is_uuid,
    "is_iso8601": is_iso8601,
    "is_length": is_length,
    "is_byte_length": is_byte_length,
    "is_fqdn": is_fqdn,
    "is_ip": is_ip,
}

CASES: list[tuple[str, str, Any, bool]] = [
    ("is_email", "foo@bar.com", None, True),
    ("is_email", "foo+bar@bar.com", None, True),
    ("is_email", "hans.m\u7aefller@test.com", None, True),
    ("is_email", '"foobar"@example.com', None, True),
    ("is_email", '"foo\\@bar"@example.com', None, True),
    ("is_email", '"@x.com', None, True),
    ("is_email", "invalidemail@", None, False),
    ("is_email", "@invalid.com", None, False),
    ("is_email", "foo@bar.com.", None, False),
    ("is_email", "foo@_bar.com", None, False),
    ("is_email", "test@email..com", None, False),
    ("is_email", "a@b.com\n", None, False),
    ("is_email", "test1@invalid.co\xa0m", None, False),
    ("is_email", "somename@\uff47\uff4d\uff41\uff49\uff4c.com", None, False),
    ("is_email", "a@[127.0.0.1]", None, False),
    ("is_email", "a@127.0.0.1", None, False),
    ("is_email", "\U0001f600@x.com", None, False),
    ("is_email", "a@\U0001f600.com", None, True),
    ("is_email", "a@x.c\U0001f600", None, False),
    ("is_email", "a@\u4f8b\u5b50.\u4e2d\u56fd", None, True),
    ("is_email", "a@xn--fiqs8s.xn--fiqz9s", None, True),
    ("is_email", "\u212a@x.com", None, True),
    ("is_email", "a" * 64 + "@x.com", None, True),
    ("is_email", "a" * 65 + "@x.com", None, False),
    ("is_email", "a@" + "b" * 63 + ".com", None, True),
    ("is_email", "a@" + "b" * 64 + ".com", None, False),
    ("is_email", "Some Name <foo@bar.com>", None, False),
    ("is_email", "Some Name <foo@bar.com>", {"allow_display_name": True}, True),
    ("is_email", "Some.Name <foo@bar.com>", {"allow_display_name": True}, False),
    ("is_email", '"Some.Name" <foo@bar.com>', {"allow_display_name": True}, True),
    ("is_email", "foo@bar.com", {"require_display_name": True}, False),
    ("is_email", "a@[127.0.0.1]", {"allow_ip_domain": True}, True),
    ("is_email", "\xfcser@x.com", {"allow_utf8_local_part": False}, False),
    ("is_email", "a@localhost", {"require_tld": False}, True),
    ("is_email", "te.st@gmail.com", {"domain_specific_validation": True}, False),
    ("is_email", "test12@gmail.com", {"domain_specific_validation": True}, True),
    ("is_email", "abc@x.com", {"blacklisted_chars": "b"}, False),
    ("is_email", "a@gmail.com", {"host_blacklist": ["gmail.com"]}, False),
    ("is_email", "a@x.com", {"host_whitelist": ["gmail.com"]}, False),
    ("is_url", "foobar.com", None, True),
    ("is_url", "http://www.foobar.com/", None, True),
    ("is_url", "http://127.0.0.1/", None, True),
    ("is_url", "http://[::1]:80", None, True),
    ("is_url", "http://[1080::8:800:200C:417A]/foo", None, True),
    ("is_url", "http://user:pass@www.foobar.com/", None, True),
    ("is_url", "http://:@foobar.com", None, False),
    ("is_url", "http://@foobar.com", None, False),
    ("is_url", "http://www.foobar.com:0/", None, False),
    ("is_url", "http://example.com:0080", None, True),
    ("is_url", "http://www.foobar.com:70000/", None, False),
    ("is_url", "http://foobar/ lol/", None, False),
    ("is_url", "http://exa\u2028mple.com", None, False),
    ("is_url", "mailto:foo@bar.com", None, False),
    ("is_url", "javascript:alert(1)", None, False),
    ("is_url", "javascript:alert(1)@example.com", None, False),
    ("is_url", "user:pass@example.com", None, True),
    ("is_url", "localhost:3000", None, False),
    ("is_url", "//foobar.com", None, False),
    ("is_url", "//foobar.com", {"allow_protocol_relative_urls": True}, True),
    ("is_url", "rtmp://foobar.com", None, False),
    ("is_url", "rtmp://foobar.com", {"protocols": ["rtmp"]}, True),
    ("is_url", "foobar.com", {"require_protocol": True}, False),
    ("is_url", "http://localhost/", {"require_tld": False}, True),
    ("is_url", "http://www.foo_bar.com/", None, False),
    ("is_url", "http://www.foo_bar.com/", {"allow_underscores": True}, True),
    ("is_url", "http://\u043a\u0443\u043b\u0456\u043a.\u0443\u043a\u0440", None, True),
    ("is_url", "http://\U0001f600.com", None, True),
    ("is_url", "http://a.com/?q=1", {"allow_query_components": False}, False),
    ("is_url", "http://a.com/#f", {"allow_fragments": False}, False),
    ("is_url", "http://example.com", {"host_whitelist": ["example.com"]}, True),
    ("is_url", "http://[::1]", {"host_whitelist": []}, False),
    ("is_url", "http://" + "a" * 2080 + ".com", None, False),
    ("is_uuid", "A987FBC9-4BED-3078-CF07-9141BA07C9F3", None, False),
    ("is_uuid", "A987FBC9-4BED-4078-8F07-9141BA07C9F3", None, True),
    ("is_uuid", "A987FBC9-4BED-4078-8F07-9141BA07C9F3", 4, True),
    ("is_uuid", "A987FBC9-4BED-4078-8F07-9141BA07C9F3", "3", False),
    ("is_uuid", "A987FBC9-4BED-3078-CF07-9141BA07C9F3", "loose", True),
    ("is_uuid", "00000000-0000-0000-0000-" + "0" * 12, None, True),
    ("is_uuid", "00000000-0000-0000-0000-" + "0" * 12, "nil", True),
    ("is_uuid", "FFFFFFFF-FFFF-FFFF-FFFF-" + "F" * 12, "max", True),
    ("is_uuid", "a987fbc9-4bed-7078-8f07-9141ba07c9f3", "7", True),
    ("is_uuid", "A987FBC9-4BED-4078-8F07-9141BA07C9F3\n", None, False),
    ("is_uuid", "A987FBC9-4BED-4078-8F07-9141BA07C9F3", "v4", False),
    ("is_uuid", "A987FBC9-4BED-4078-8F07-9141BA07C9F3", 9, False),
    ("is_iso8601", "2009-05-19", None, True),
    ("is_iso8601", "20090519", None, True),
    ("is_iso8601", "2009-123", None, True),
    ("is_iso8601", "2009-W21-2", None, True),
    ("is_iso8601", "2009-05-19 14:39:22-06:00", None, True),
    ("is_iso8601", "2010-02-18T16:23:48,444", None, True),
    ("is_iso8601", "2007-04-05T24:00", None, True),
    ("is_iso8601", "2009-05-19T240030", None, True),
    ("is_iso8601", "2009-05-19\n", None, True),
    ("is_iso8601", "2009-05-19\u300014:39", None, True),
    ("is_iso8601", "2009-05-19 14:39", {"strictSeparator": True}, False),
    ("is_iso8601", "2009-05-19T14:39", {"strictSeparator": True}, True),
    ("is_iso8601", "2009-0519", None, False),
    ("is_iso8601", "2009-02-29", None, True),
    ("is_iso8601", "2009-02-29", {"strict": True}, False),
    ("is_iso8601", "2008-02-29", {"strict": True}, True),
    ("is_iso8601", "2009-366", {"strict": True}, False),
    ("is_iso8601", "2020-366", {"strict": True}, True),
    ("is_iso8601", "0099-01-01", {"strict": True}, False),
    ("is_iso8601", "2021-366\t10", {"strict": True}, False),
    ("is_iso8601", "\u0662\u0660\u0660\u0669-05-19", None, False),
    ("is_length", "abc", {"min": 3, "max": 3}, True),
    ("is_length", "\U0001f600", {"max": 1}, True),
    ("is_length", "\u2764\ufe0f", {"max": 1}, True),
    ("is_length", "\ufe0f\ufe0f", {"max": 1}, False),
    ("is_length", "\U0001f468\u200d\U0001f469\u200d\U0001f467", {"max": 4}, False),
    ("is_length", "\U0001f468\u200d\U0001f469\u200d\U0001f467", {"max": 5}, True),
    ("is_length", "\ud800", {"min": 1, "max": 1}, True),
    ("is_length", "abc", {"min": 1, "max": 6, "discreteLengths": [1, 3, 5]}, True),
    ("is_length", "ab", {"min": 1, "max": 6, "discreteLengths": [1, 3, 5]}, False),
    ("is_length", "", None, True),
    ("is_byte_length", "abc", {"min": 3, "max": 3}, True),
    ("is_byte_length", "\u4e2d", {"max": 2}, False),
    ("is_byte_length", "\u4e2d", {"min": 3, "max": 3}, True),
    ("is_byte_length", "\U0001f600", {"min": 4, "max": 4}, True),
    ("is_byte_length", "abc", None, False),
    ("is_fqdn", "example.com", None, True),
    ("is_fqdn", "localhost", None, False),
    ("is_fqdn", "localhost", {"require_tld": False}, True),
    ("is_fqdn", "example.com.", {"allow_trailing_dot": True}, True),
    ("is_fqdn", "*.example.com", {"allow_wildcard": True}, True),
    ("is_fqdn", "example.123", None, False),
    ("is_fqdn", "example.123", {"allow_numeric_tld": True}, True),
    ("is_fqdn", "-a.com", None, False),
    ("is_ip", "127.0.0.1", None, True),
    ("is_ip", "256.0.0.1", None, False),
    ("is_ip", "::1", None, True),
    ("is_ip", "fe80::1234%eth0", None, True),
    ("is_ip", "::ffff:127.0.0.1", {"version": 6}, True),
    ("is_ip", "127.0.0.1", 6, False),
    ("is_ip", "127.0.0.1\n", None, False),
]


@pytest.mark.parametrize(("name", "value", "options", "expected"), CASES)
def test_matches_validator_js(name: str, value: str, options: Any, expected: bool) -> None:
    assert FUNCTIONS[name](value, options) is expected


RAISING_CASES: list[tuple[str, str, Any, type[Exception]]] = [
    ("is_email", "a\ud800@x.com", None, ValueError),
    ("is_uuid", "A987FBC9-4BED-4078-8F07-9141BA07C9F3", "toString", TypeError),
    ("is_byte_length", "\ud800", {}, ValueError),
]


@pytest.mark.parametrize(("name", "value", "options", "error"), RAISING_CASES)
def test_raises_where_validator_js_throws(
    name: str, value: str, options: Any, error: type[Exception]
) -> None:
    with pytest.raises(error):
        FUNCTIONS[name](value, options)


@pytest.mark.parametrize("name", sorted(FUNCTIONS))
def test_rejects_non_string_input(name: str) -> None:
    fn: Any = FUNCTIONS[name]
    with pytest.raises(TypeError):
        fn(123, None)
