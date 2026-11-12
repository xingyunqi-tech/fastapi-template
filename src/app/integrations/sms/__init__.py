"""短信验证码：生成、发送频率限制、存储与校验（nestjs SmsService）。"""

import asyncio
import hmac
import logging
import math
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
lazy from collections.abc import AsyncIterator

from app.integrations.sms.aliyun import AliyunSmsProvider, SendSmsResult
from app.integrations.sms.masking import mask_phone
lazy from app.config import SmsSettings
lazy from app.infrastructure.cache import Cache
lazy from app.infrastructure.redis import LockService

__all__ = [
    "AliyunSmsProvider",
    "SendCodeResult",
    "SendSmsResult",
    "SmsCodeType",
    "SmsService",
    "VerifyCodeResult",
    "mask_phone",
]

logger = logging.getLogger("app.sms")

RESEND_MESSAGE: Final = "Please wait before requesting a new code"


class SmsCodeType(StrEnum):
    LOGIN = "login"
    REGISTER = "register"
    RESET_PASSWORD = "reset_password"  # noqa: S105
    CHANGE_PHONE = "change_phone"
    VERIFY_IDENTITY = "verify_identity"


@dataclass(frozen=True, slots=True)
class SendCodeResult:
    success: bool
    message: str
    retry_after: int | None = None


@dataclass(frozen=True, slots=True)
class VerifyCodeResult:
    success: bool
    message: str


class _KeyedLocks:
    """同一键的协程在本进程内串行执行。"""

    def __init__(self) -> None:
        self._locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        lock, users = self._locks.get(key, (asyncio.Lock(), 0))
        self._locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            lock, users = self._locks[key]
            if users <= 1:
                del self._locks[key]
            else:
                self._locks[key] = (lock, users - 1)


_verify_locks = _KeyedLocks()


def code_cache_key(phone: str, type_: SmsCodeType) -> str:
    return f"sms:code:{type_}:{phone}"


def _now_ms() -> int:
    return int(time.time() * 1000)


class SmsService:
    def __init__(
        self,
        settings: SmsSettings,
        cache: Cache,
        locks: LockService,
        provider: AliyunSmsProvider,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.locks = locks
        self.provider = provider

    def _generate_code(self) -> str:
        return "".join(str(secrets.randbelow(10)) for _ in range(self.settings.code_length))

    async def send_code(self, phone: str, type_: SmsCodeType) -> SendCodeResult:
        key = code_cache_key(phone, type_)
        existing: Any = await self.cache.get(key)
        interval = self.settings.resend_interval
        if existing:
            elapsed = (_now_ms() - existing["createdAt"]) / 1000
            if elapsed < interval:
                return SendCodeResult(False, RESEND_MESSAGE, math.ceil(interval - elapsed))

        lock_key = f"sms:send:{type_}:{phone}"
        token = await self.locks.try_acquire(lock_key, interval * 1000)
        if token is None:
            return SendCodeResult(False, RESEND_MESSAGE, interval)

        code = self._generate_code()
        try:
            result = await self.provider.send_sms(phone, {"code": code})
            if not result.success:
                await self.locks.release(lock_key, token)
                logger.error("Failed to send SMS to %s: %s", mask_phone(phone), result.message)
                return SendCodeResult(False, result.message or "Failed to send verification code")
            await self.cache.set(
                key,
                {"code": code, "attempts": 0, "createdAt": _now_ms()},
                self.settings.code_expires * 1000,
            )
        except BaseException:
            await self.locks.release(lock_key, token)
            raise
        logger.info("Verification code sent to %s for %s", mask_phone(phone), type_)
        return SendCodeResult(True, "Verification code sent successfully")

    async def verify_code(self, phone: str, code: str, type_: SmsCodeType) -> VerifyCodeResult:
        key = code_cache_key(phone, type_)
        async with _verify_locks.hold(key):
            return await self._verify_unlocked(key, phone, code, type_)

    async def _verify_unlocked(
        self, key: str, phone: str, code: str, type_: SmsCodeType
    ) -> VerifyCodeResult:
        stored: Any = await self.cache.get(key)
        if not stored:
            return VerifyCodeResult(False, "Verification code expired or not found")
        if stored["attempts"] >= self.settings.max_attempts:
            await self.cache.delete(key)
            return VerifyCodeResult(False, "Too many failed attempts, please request a new code")
        if not hmac.compare_digest(str(stored["code"]), code):
            stored["attempts"] += 1
            remaining = max(
                0, self.settings.code_expires * 1000 - (_now_ms() - stored["createdAt"])
            )
            if remaining > 0:
                await self.cache.set(key, stored, remaining)
            return VerifyCodeResult(False, "Invalid verification code")
        await self.cache.delete(key)
        logger.info("Verification code verified for %s (%s)", mask_phone(phone), type_)
        return VerifyCodeResult(True, "Verification code is valid")
