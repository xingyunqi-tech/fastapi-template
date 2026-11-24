"""约定 §5.1 access 令牌：EdDSA 签发与校验。"""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid4
lazy from datetime import timedelta

import jwt

from app.security.keys import load_public_key, thumbprint

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from app.security.jwks import JwksKeySet
    from app.security.keys import SigningKey

ALGORITHM: Final = "EdDSA"
LEEWAY_SECONDS: Final = 30
_REQUIRED_CLAIMS: Final = ["iss", "aud", "sub", "sid", "exp", "iat", "jti", "auth_time"]


class TokenExpiredError(Exception):
    pass


class InvalidTokenError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Principal:
    """已校验的 access 令牌。"""

    sub: UUID
    sid: UUID
    roles: tuple[str, ...]
    email_verified: bool
    auth_time: int


class TokenSigner:
    def __init__(self, key: SigningKey, *, issuer: str, audience: str, ttl: timedelta) -> None:
        self.key = key
        self.issuer = issuer
        self.audience = audience
        self.ttl = ttl

    @property
    def expires_in(self) -> int:
        return int(self.ttl.total_seconds())

    def sign(
        self,
        *,
        sub: UUID,
        sid: UUID,
        roles: Iterable[str],
        email_verified: bool,
        auth_time: int,
    ) -> str:
        issued_at = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": str(sub),
            "sid": str(sid),
            "roles": list(roles),
            "email_verified": email_verified,
            "auth_time": auth_time,
            "iat": issued_at,
            "exp": issued_at + self.expires_in,
            "jti": str(uuid4()),
        }
        return jwt.encode(
            claims, self.key.private_key, algorithm=ALGORITHM, headers={"kid": self.key.kid}
        )


def _uuid(value: Any) -> UUID:
    if not isinstance(value, str):
        raise InvalidTokenError
    try:
        return UUID(value)
    except ValueError:
        raise InvalidTokenError from None


def _principal(claims: Mapping[str, Any]) -> Principal:
    roles = claims.get("roles")
    email_verified = claims.get("email_verified")
    auth_time = claims.get("auth_time")
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise InvalidTokenError
    if not isinstance(email_verified, bool) or type(auth_time) is not int:
        raise InvalidTokenError
    return Principal(
        sub=_uuid(claims.get("sub")),
        sid=_uuid(claims.get("sid")),
        roles=tuple(roles),
        email_verified=email_verified,
        auth_time=auth_time,
    )


class TokenVerifier:
    """按头部 `kid` 取公钥校验；只接受 `alg: EdDSA`，`iss` / `aud` / `exp` 容忍 30 秒偏差。

    签发方使用本地公钥；验证方从 `jwks` 取公钥。
    """

    def __init__(
        self,
        keys: Iterable[Ed25519PublicKey] = (),
        *,
        issuer: str,
        audience: str,
        jwks: JwksKeySet | None = None,
    ) -> None:
        self.local_keys = {thumbprint(key): key for key in keys}
        self.issuer = issuer
        self.audience = audience
        self.jwks = jwks

    @classmethod
    def from_pems(cls, pems: Iterable[str], *, issuer: str, audience: str) -> TokenVerifier:
        return cls([load_public_key(pem) for pem in pems], issuer=issuer, audience=audience)

    @property
    def keys(self) -> dict[str, Ed25519PublicKey]:
        return self.jwks.keys if self.jwks is not None else self.local_keys

    async def verify(self, token: str) -> Principal:
        """JWKS 不可用且没有缓存的公钥时抛 `JwksUnavailableError`。"""
        kid = _kid(token)
        key = await self.jwks.get(kid) if self.jwks is not None else self.local_keys.get(kid)
        return self._decode(token, key)

    def subject(self, token: str) -> UUID | None:
        """只用已缓存的公钥校验，通过时返回 `sub`；用于日志、审计与限流主体。"""
        try:
            return self._decode(token, self.keys.get(_kid(token))).sub
        except InvalidTokenError, TokenExpiredError:
            return None

    def _decode(self, token: str, key: Ed25519PublicKey | None) -> Principal:
        if key is None:
            raise InvalidTokenError
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=self.audience,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": _REQUIRED_CLAIMS, "strict_aud": True},
            )
        except jwt.ExpiredSignatureError:
            raise TokenExpiredError from None
        except jwt.PyJWTError:
            raise InvalidTokenError from None
        return _principal(claims)


def _kid(token: str) -> str:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise InvalidTokenError from None
    kid = header.get("kid")
    if header.get("alg") != ALGORITHM or not isinstance(kid, str):
        raise InvalidTokenError
    return kid
