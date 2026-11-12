from datetime import UTC, datetime

import pytest

from app.config import AlipaySettings, SettingsError, SmsSettings, get_settings
from app.integrations.alipay import build_sign_string, format_private_key, to_beijing_time
from app.integrations.sms import mask_phone
from app.integrations.sms.aliyun import sign_request


def test_aliyun_signature_matches_sdk() -> None:
    headers = sign_request(
        method="POST",
        host="dysmsapi.aliyuncs.com",
        action="SendSms",
        version="2017-05-25",
        query={
            "PhoneNumbers": "13800138000",
            "SignName": "测试签名(abc)",
            "TemplateCode": "SMS_1",
            "TemplateParam": '{"code":"123456"}',
        },
        access_key_id="AKID",
        access_key_secret="SECRET",
        date="2026-11-11T00:00:00Z",
        nonce="abc",
    )
    assert headers["authorization"] == (
        "ACS3-HMAC-SHA256 Credential=AKID,"
        "SignedHeaders=host;x-acs-action;x-acs-content-sha256;x-acs-date;"
        "x-acs-signature-nonce;x-acs-version,"
        "Signature=0e408f127b9737e543e3b81dd7aaeffab930c0ed05ec842611399d71706260d4"
    )


def test_alipay_sign_string_skips_empty_values() -> None:
    assert build_sign_string({"b": "2", "a": "1", "c": ""}) == "a=1&b=2"


def test_alipay_private_key_is_wrapped_as_pkcs8_pem() -> None:
    assert format_private_key("A" * 70) == (
        "-----BEGIN " + f"PRIVATE KEY-----\n{'A' * 64}\n{'A' * 6}\n-----END PRIVATE KEY-----"
    )
    pem = "-----BEGIN " + "PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    assert format_private_key(pem) == pem


def test_alipay_timestamp_is_beijing_time() -> None:
    assert to_beijing_time(datetime(2026, 11, 11, 20, 30, 5, tzinfo=UTC)) == "2026-11-12 04:30:05"


@pytest.mark.parametrize(
    ("phone", "masked"), [("13800138000", "138****8000"), ("123", "***"), ("", "[invalid]")]
)
def test_mask_phone(phone: str, masked: str) -> None:
    assert mask_phone(phone) == masked


def test_sms_defaults() -> None:
    settings = SmsSettings()
    assert (
        settings.code_expires,
        settings.code_length,
        settings.max_attempts,
        settings.resend_interval,
        settings.endpoint,
        settings.mock_mode,
    ) == (300, 6, 5, 60, "dysmsapi.aliyuncs.com", False)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SMS_CODE_LENGTH", "9"),
        ("SMS_CODE_EXPIRES", "30"),
        ("SMS_MAX_ATTEMPTS", "11"),
        ("SMS_RESEND_INTERVAL", "10"),
        ("SMS_MOCK_MODE", "yes"),
        ("ALIPAY_SIGN_TYPE", "MD5"),
    ],
)
def test_invalid_login_settings_fail_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(SettingsError):
        get_settings()


@pytest.mark.parametrize(
    ("env", "gateway"),
    [
        ({}, "https://openapi.alipay.com/gateway.do"),
        ({"ALIPAY_SANDBOX": "true"}, "https://openapi.alipaydev.com/gateway.do"),
        ({"ALIPAY_GATEWAY": "https://gw.example.com/x"}, "https://gw.example.com/x"),
    ],
)
def test_alipay_gateway(monkeypatch: pytest.MonkeyPatch, env: dict[str, str], gateway: str) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert AlipaySettings().gateway == gateway
