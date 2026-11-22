from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.common.errors import AppError
from app.config import AlipaySettings, SettingsError, SmsSettings, get_settings
from app.identity.providers.alipay import build_sign_string, raw_member, to_beijing_time
from app.identity.providers.alipay.keys import load_private_key, load_public_key
from app.identity.providers.sms import (
    COOLDOWN_MS,
    DAILY_LIMIT,
    FAILURE_LIMIT,
    MAX_ATTEMPTS,
    LogSmsSender,
    SmsCodes,
    SmsScene,
    digest_key,
)
from app.identity.providers.sms.aliyun import aliyun_phone, sign_request
from app.identity.providers.sms.masking import mask_phone
from app.identity.providers.sms.store import MemorySmsStore
from app.infrastructure.observability import strip_breadcrumb_query, strip_span_query
from tests import keys

PHONE = "+8613800138000"


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


@pytest.mark.parametrize(
    ("phone", "expected"), [(PHONE, "13800138000"), ("+85291234567", "85291234567")]
)
def test_aliyun_phone_format(phone: str, expected: str) -> None:
    assert aliyun_phone(phone) == expected


@pytest.mark.parametrize(
    ("phone", "masked"), [("13800138000", "138****8000"), ("123", "***"), ("", "[invalid]")]
)
def test_mask_phone(phone: str, masked: str) -> None:
    assert mask_phone(phone) == masked


class TestAlipay:
    def test_sign_string_skips_empty_values(self) -> None:
        assert build_sign_string({"b": "2", "a": "1", "c": ""}) == "a=1&b=2"

    def test_timestamp_is_beijing_time(self) -> None:
        moment = datetime(2026, 11, 11, 20, 30, 5, tzinfo=UTC)
        assert to_beijing_time(moment) == "2026-11-12 04:30:05"

    def test_keys_accept_pem_or_bare_base64(self) -> None:
        private, public = keys.alipay_app_pair()
        bare = "".join(line for line in public.splitlines() if not line.startswith("-----"))
        assert load_public_key(bare).public_numbers() == load_public_key(public).public_numbers()
        assert load_private_key(private.replace("\n", "\\n")).key_size == 2048

    def test_raw_member_keeps_original_text(self) -> None:
        text = '{"r" : {"b":1, "a":"x"},"sign":"s"}'
        assert raw_member(text, "r") == '{"b":1, "a":"x"}'
        assert raw_member(text, "missing") is None

    def test_settings_reject_non_rsa_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALIPAY_PRIVATE_KEY", keys.SIGNING_KEY)
        monkeypatch.setenv("ALIPAY_PUBLIC_KEY", "nope")
        with pytest.raises(SettingsError) as error:
            get_settings()
        assert len([e for e in error.value.errors if e.startswith("ALIPAY_")]) == 2

    def test_enabled_requires_all_credentials(self) -> None:
        private, public = keys.alipay_app_pair()
        env: dict[str, Any] = {"ALIPAY_APP_ID": "a", "ALIPAY_PRIVATE_KEY": private}
        assert not AlipaySettings.model_validate(env).enabled
        assert AlipaySettings.model_validate({**env, "ALIPAY_PUBLIC_KEY": public}).enabled


class TestSmsSettings:
    def test_default_provider_is_log(self) -> None:
        assert SmsSettings().provider == "log"

    def test_aliyun_requires_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SMS_PROVIDER", "aliyun")
        monkeypatch.setenv("SMS_ALIYUN_ACCESS_KEY_ID", "id")
        with pytest.raises(SettingsError, match="SMS_ALIYUN_ACCESS_KEY_SECRET"):
            get_settings()

    def test_unknown_provider_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SMS_PROVIDER", "twilio")
        with pytest.raises(SettingsError):
            get_settings()


class RecordingSender:
    def __init__(self) -> None:
        self.codes: list[str] = []
        self.fail = False

    async def send(self, phone: str, code: str) -> None:
        if self.fail:
            raise AppError("common.badGateway")
        self.codes.append(code)


@pytest.fixture
def sender() -> RecordingSender:
    return RecordingSender()


@pytest.fixture
def store() -> MemorySmsStore:
    return MemorySmsStore()


@pytest.fixture
def codes(store: MemorySmsStore, sender: RecordingSender) -> SmsCodes:
    return SmsCodes(store, sender, b"k" * 32)


async def error_key(awaitable: Any) -> tuple[str, dict[str, str]]:
    with pytest.raises(AppError) as error:
        await awaitable
    return error.value.error_key, error.value.headers


class TestSmsCodes:
    async def test_code_is_six_digits_and_single_use(
        self, codes: SmsCodes, sender: RecordingSender
    ) -> None:
        await codes.send(PHONE, SmsScene.LOGIN)
        code = sender.codes[-1]
        assert len(code) == 6
        assert code.isdigit()

        await codes.verify(PHONE, SmsScene.LOGIN, code)
        key, _ = await error_key(codes.verify(PHONE, SmsScene.LOGIN, code))
        assert key == "auth.verificationCodeInvalid"

    async def test_code_is_bound_to_scene(self, codes: SmsCodes, sender: RecordingSender) -> None:
        await codes.send(PHONE, SmsScene.LOGIN)
        key, _ = await error_key(codes.verify(PHONE, SmsScene.BIND_PHONE, sender.codes[-1]))
        assert key == "auth.verificationCodeInvalid"
        await codes.verify(PHONE, SmsScene.LOGIN, sender.codes[-1])

    async def test_cooldown_returns_retry_after(self, codes: SmsCodes) -> None:
        await codes.send(PHONE, SmsScene.LOGIN)
        key, headers = await error_key(codes.send(PHONE, SmsScene.BIND_PHONE))
        assert key == "auth.sendTooFrequent"
        assert 0 < int(headers["Retry-After"]) <= COOLDOWN_MS // 1000

    async def test_daily_limit(self, codes: SmsCodes, store: MemorySmsStore) -> None:
        for _ in range(DAILY_LIMIT):
            await codes.send(PHONE, SmsScene.LOGIN)
            await store.delete(f"sms:cooldown:{PHONE}")
        key, headers = await error_key(codes.send(PHONE, SmsScene.LOGIN))
        assert key == "auth.sendTooFrequent"
        assert int(headers["Retry-After"]) > 0

    async def test_send_failure_releases_cooldown_and_quota(
        self, codes: SmsCodes, sender: RecordingSender, store: MemorySmsStore
    ) -> None:
        sender.fail = True
        key, _ = await error_key(codes.send(PHONE, SmsScene.LOGIN))
        assert key == "common.badGateway"
        assert await store.ttl_ms(f"sms:cooldown:{PHONE}") is None
        daily = f"sms:daily:{datetime.now(UTC):%Y%m%d}:{PHONE}"
        assert await store.increment(daily, 60_000) == 1

    async def test_code_is_invalidated_after_max_attempts(
        self, codes: SmsCodes, sender: RecordingSender
    ) -> None:
        await codes.send(PHONE, SmsScene.LOGIN)
        code = sender.codes[-1]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(MAX_ATTEMPTS - 1):
            await error_key(codes.verify(PHONE, SmsScene.LOGIN, wrong))
        await codes.verify(PHONE, SmsScene.LOGIN, code)

        await codes.store.delete(f"sms:cooldown:{PHONE}")
        await codes.send(PHONE, SmsScene.LOGIN)
        code = sender.codes[-1]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(MAX_ATTEMPTS):
            await error_key(codes.verify(PHONE, SmsScene.LOGIN, wrong))
        key, _ = await error_key(codes.verify(PHONE, SmsScene.LOGIN, code))
        assert key == "auth.verificationCodeInvalid"

    async def test_failures_lock_the_phone(self, codes: SmsCodes, sender: RecordingSender) -> None:
        for _ in range(FAILURE_LIMIT):
            key, _ = await error_key(codes.verify(PHONE, SmsScene.LOGIN, "123456"))
            assert key == "auth.verificationCodeInvalid"
        key, headers = await error_key(codes.verify(PHONE, SmsScene.LOGIN, "123456"))
        assert key == "auth.verificationLocked"
        assert int(headers["Retry-After"]) > 3500


async def test_log_sender_hides_code_in_production(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="app.sms")
    await LogSmsSender(reveal_code=True).send(PHONE, "123456")
    await LogSmsSender(reveal_code=False).send(PHONE, "654321")
    assert "123456" in caplog.text
    assert "654321" not in caplog.text


def test_code_digest_is_keyed_and_bound_to_phone_and_scene() -> None:
    codes = SmsCodes(MemorySmsStore(), RecordingSender(), digest_key(keys.SIGNING_KEY))
    other = SmsCodes(MemorySmsStore(), RecordingSender(), digest_key(keys.OTHER_SIGNING_KEY))
    digest = codes._digest(PHONE, SmsScene.LOGIN, "123456")
    assert digest == SmsCodes(
        MemorySmsStore(), RecordingSender(), digest_key(keys.SIGNING_KEY)
    )._digest(PHONE, SmsScene.LOGIN, "123456")
    assert digest != other._digest(PHONE, SmsScene.LOGIN, "123456")
    assert digest != codes._digest(PHONE, SmsScene.BIND_PHONE, "123456")
    assert digest != codes._digest("+8613800138001", SmsScene.LOGIN, "123456")


class RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, str] = {}

    def is_recording(self) -> bool:
        return True

    def set_attribute(self, name: str, value: str) -> None:
        self.attributes[name] = value


class TestOutboundUrlRedaction:
    URL = "https://api.weixin.qq.com/sns/jscode2session?appid=a&secret=s&js_code=c#f"

    def test_span_url_has_no_query(self) -> None:
        span = RecordingSpan()
        strip_span_query(span, SimpleNamespace(url=self.URL))  # type: ignore[arg-type]
        assert span.attributes == {
            "url.full": "https://api.weixin.qq.com/sns/jscode2session",
            "http.url": "https://api.weixin.qq.com/sns/jscode2session",
        }

    def test_breadcrumb_has_no_query(self) -> None:
        crumb = strip_breadcrumb_query(
            {
                "category": "httplib",
                "data": {"url": self.URL, "http.query": "secret=s", "http.fragment": "f"},
            },
            None,
        )
        assert crumb["data"] == {"url": "https://api.weixin.qq.com/sns/jscode2session"}
