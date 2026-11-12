"""密码哈希（argon2id / bcrypt）、ms 时长解析与 jsonwebtoken 兼容的 JWT 签发校验。"""

import hashlib
import math
import re
import secrets
import time
import warnings
from dataclasses import dataclass
from typing import Any, Final

import anyio.to_thread
import jwt
from jwt.warnings import InsecureKeyLengthWarning
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from app.config import PasswordHasher

BCRYPT_ROUNDS: Final = 10
_MS_RE: Final = re.compile(
    r"(-?(?:\d+)?\.?\d+) *(milliseconds?|msecs?|ms|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h"
    r"|days?|d|weeks?|w|years?|yrs?|y)?",
    re.IGNORECASE,
)
_UNIT_MS: Final = {
    "y": 365.25 * 86_400_000,
    "w": 7 * 86_400_000,
    "d": 86_400_000,
    "h": 3_600_000,
    "m": 60_000,
    "s": 1_000,
}


def ms(value: str) -> float:
    """`ms` 2.1 的字符串解析：无单位为毫秒；无法解析时抛 ValueError。"""
    match = _MS_RE.fullmatch(value) if len(value) <= 100 else None
    if match is None:
        raise ValueError(f"Invalid duration: {value!r}")
    number = float(match.group(1))
    unit = (match.group(2) or "ms").lower()
    if unit in {"milliseconds", "millisecond", "msecs", "msec", "ms"}:
        return number
    key = "m" if unit.startswith("mi") and unit not in {"ms"} else unit[0]
    return number * _UNIT_MS[key]


def now_ms() -> int:
    return int(time.time() * 1000)


def generate_session_hash() -> str:
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()


def password_fingerprint(password_hash: str | None) -> str:
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class VerifyResult:
    valid: bool
    updated_hash: str | None


class Passwords:
    """客户端传 MD5，服务端存 argon2id（默认）或 bcrypt；可验证两种格式并在需要时重哈希。"""

    def __init__(self, hasher: PasswordHasher) -> None:
        bcrypt = BcryptHasher(rounds=BCRYPT_ROUNDS)
        argon2 = Argon2Hasher()
        preferred = bcrypt if hasher is PasswordHasher.BCRYPT else argon2
        fallback = argon2 if hasher is PasswordHasher.BCRYPT else bcrypt
        self._hash = PasswordHash((preferred, fallback))

    def hash(self, md5_password: str) -> str:
        return self._hash.hash(md5_password)

    async def hash_async(self, md5_password: str) -> str:
        return await anyio.to_thread.run_sync(self.hash, md5_password)

    async def verify_md5_async(self, md5_password: str, password_hash: str) -> VerifyResult:
        return await anyio.to_thread.run_sync(self.verify_md5, md5_password, password_hash)

    def verify_md5(self, md5_password: str, password_hash: str) -> VerifyResult:
        """依次尝试原值与大写 MD5；验证通过且哈希格式需要升级时返回新哈希。"""
        for candidate in dict.fromkeys([md5_password, md5_password.upper()]):
            try:
                valid, updated = self._hash.verify_and_update(candidate, password_hash)
            except Exception:
                return VerifyResult(valid=False, updated_hash=None)
            if valid:
                return VerifyResult(valid=True, updated_hash=updated)
        return VerifyResult(valid=False, updated_hash=None)


class TokenExpiredError(Exception):
    pass


class InvalidTokenError(Exception):
    pass


class Jwt:
    """与 jsonwebtoken 一致：HS256，`iat` 为秒，`exp = iat + floor(ms(expiresIn) / 1000)`。"""

    @staticmethod
    def sign(payload: dict[str, Any], secret: str, expires_in: str) -> str:
        issued_at = math.floor(time.time())
        claims = {**payload, "iat": issued_at, "exp": math.floor(issued_at + ms(expires_in) / 1000)}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", InsecureKeyLengthWarning)
            return jwt.encode(claims, secret, algorithm="HS256")

    @staticmethod
    def verify(token: str, secret: str) -> dict[str, Any]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", InsecureKeyLengthWarning)
                claims: dict[str, Any] = jwt.decode(
                    token,
                    secret,
                    algorithms=["HS256", "HS384", "HS512"],
                    options={"require": [], "verify_iat": False},
                )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError from exc
        except jwt.PyJWTError as exc:
            raise InvalidTokenError from exc
        return claims
