"""约定 §11.1 短信验证码：发送冷却与每日上限、尝试次数、失败锁定。"""

import hmac
import logging
import math
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.common.errors import AppError
from app.config.sms import SmsProvider
from app.identity.providers.sms.aliyun import AliyunSmsSender
from app.identity.providers.sms.masking import mask_phone
lazy from app.common.context import AppContext
lazy from app.identity.providers.sms.store import SmsStore

logger = logging.getLogger("app.sms")

CODE_LENGTH: Final = 6
CODE_TTL_MS: Final = 5 * 60 * 1000
MAX_ATTEMPTS: Final = 5
COOLDOWN_MS: Final = 60 * 1000
DAILY_LIMIT: Final = 10
FAILURE_WINDOW_MS: Final = 60 * 60 * 1000
FAILURE_LIMIT: Final = 10
LOCK_MS: Final = 60 * 60 * 1000


class SmsScene(StrEnum):
    LOGIN = "login"
    BIND_PHONE = "bind_phone"


class SmsSender(Protocol):
    async def send(self, phone: str, code: str) -> None:
        """发送失败时 502 `common.badGateway`。"""
        ...


class LogSmsSender:
    """只写日志；生产环境不输出验证码。"""

    def __init__(self, *, reveal_code: bool) -> None:
        self.reveal_code = reveal_code

    async def send(self, phone: str, code: str) -> None:
        if self.reveal_code:
            logger.info("SMS code for %s: %s", mask_phone(phone), code)
        else:
            logger.warning("SMS_PROVIDER=log, code for %s is not delivered", mask_phone(phone))


def build_sms_sender(context: AppContext) -> SmsSender:
    settings = context.settings
    if settings.sms.provider is SmsProvider.ALIYUN:
        return AliyunSmsSender(settings.sms, context.http_transport)
    return LogSmsSender(reveal_code=not settings.app.is_production)


def digest_key(signing_key: str) -> bytes:
    """验证码 HMAC 密钥，由 `AUTH_SIGNING_KEY` 经 HKDF-SHA256 派生。"""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"sms-code").derive(
        signing_key.encode()
    )


def _retry_after(ms: int) -> dict[str, str]:
    return {"Retry-After": str(max(1, math.ceil(ms / 1000)))}


def _until_next_utc_day(now: datetime) -> int:
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return math.ceil((tomorrow - now).total_seconds() * 1000)


class SmsCodes:
    """键为 `sms:*`；每日上限按 UTC 日期计。"""

    def __init__(self, store: SmsStore, sender: SmsSender, key: bytes) -> None:
        self.store = store
        self.sender = sender
        self.key = key

    def _digest(self, phone: str, scene: SmsScene, code: str) -> str:
        message = f"{phone}:{scene}:{code}".encode()
        return hmac.new(self.key, message, "sha256").hexdigest()

    async def send(self, phone: str, scene: SmsScene) -> None:
        """冷却中或超过当日上限时 429 `auth.sendTooFrequent`。"""
        store = self.store
        cooldown = f"sms:cooldown:{phone}"
        if not await store.set_nx(cooldown, COOLDOWN_MS):
            remaining = await store.ttl_ms(cooldown) or COOLDOWN_MS
            raise AppError("auth.sendTooFrequent", headers=_retry_after(remaining))
        now = datetime.now(UTC)
        daily = f"sms:daily:{now:%Y%m%d}:{phone}"
        until_tomorrow = _until_next_utc_day(now)
        if await store.increment(daily, until_tomorrow + 60_000) > DAILY_LIMIT:
            await store.decrement(daily)
            raise AppError("auth.sendTooFrequent", headers=_retry_after(until_tomorrow))
        code = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
        try:
            await self.sender.send(phone, code)
        except BaseException:
            await store.delete(cooldown)
            await store.decrement(daily)
            raise
        await store.set(f"sms:code:{scene}:{phone}", self._digest(phone, scene, code), CODE_TTL_MS)
        await store.delete(f"sms:attempts:{scene}:{phone}")

    async def verify(self, phone: str, scene: SmsScene, code: str) -> None:
        """锁定中 429 `auth.verificationLocked`；错误、过期或超过尝试次数 422。"""
        store = self.store
        lock = f"sms:locked:{phone}"
        locked = await store.ttl_ms(lock)
        if locked is not None:
            raise AppError("auth.verificationLocked", headers=_retry_after(locked))
        if await store.check_code(
            f"sms:code:{scene}:{phone}",
            f"sms:attempts:{scene}:{phone}",
            self._digest(phone, scene, code),
            MAX_ATTEMPTS,
        ):
            return
        failures = f"sms:failures:{phone}"
        if await store.increment(failures, FAILURE_WINDOW_MS) >= FAILURE_LIMIT:
            await store.set(lock, "1", LOCK_MS)
            await store.delete(failures)
        raise AppError("auth.verificationCodeInvalid")


def sms_codes(context: AppContext) -> SmsCodes:
    signing_key = context.settings.auth.signing_key.get_secret_value()
    return SmsCodes(context.sms_store, build_sms_sender(context), digest_key(signing_key))
