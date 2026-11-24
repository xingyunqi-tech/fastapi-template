import re
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Any, Final, Self

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from pydantic import AnyHttpUrl, BeforeValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import NoDecode

from app.config.base import Duration, SettingsGroup
from app.security.passwords import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH

_PEM_BLOCK: Final = re.compile(
    r"-----BEGIN PUBLIC KEY-----[\s\S]+?-----END PUBLIC KEY-----", re.MULTILINE
)


def _split_public_keys(value: Any) -> Any:
    if isinstance(value, str):
        blocks = _PEM_BLOCK.findall(value.replace("\\n", "\n"))
        if value.strip() and not blocks:
            raise ValueError("must contain PEM public keys")
        return blocks
    return value


PublicKeys = Annotated[list[str], NoDecode, BeforeValidator(_split_public_keys)]


class AuthRole(StrEnum):
    ISSUER = "issuer"
    VERIFIER = "verifier"


class AuthSettings(SettingsGroup):
    """约定 §9：`AUTH_SIGNING_KEY` 为签发方，`AUTH_JWKS_URL` 为验证方，二者恰好设置一个。"""

    issuer: str = Field(min_length=1, validation_alias="AUTH_ISSUER")
    audience: str = Field(min_length=1, validation_alias="AUTH_AUDIENCE")
    signing_key: SecretStr | None = Field(default=None, validation_alias="AUTH_SIGNING_KEY")
    jwks_url: AnyHttpUrl | None = Field(default=None, validation_alias="AUTH_JWKS_URL")
    verify_keys: PublicKeys = Field(default_factory=list, validation_alias="AUTH_VERIFY_KEYS")
    access_ttl: Duration = Field(default=timedelta(minutes=15), validation_alias="AUTH_ACCESS_TTL")
    refresh_ttl: Duration = Field(default=timedelta(hours=720), validation_alias="AUTH_REFRESH_TTL")
    hash_concurrency: int = Field(default=2, ge=1, validation_alias="PASSWORD_HASH_CONCURRENCY")
    seed_admin_email: str | None = Field(default=None, validation_alias="SEED_ADMIN_EMAIL")
    seed_admin_password: SecretStr | None = Field(
        default=None, validation_alias="SEED_ADMIN_PASSWORD"
    )

    @field_validator("signing_key")
    @classmethod
    def _ed25519_private_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        pem = value.get_secret_value().replace("\\n", "\n")
        try:
            key = load_pem_private_key(pem.encode(), password=None)
        except ValueError, TypeError:
            raise ValueError("must be an Ed25519 private key in PEM format") from None
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("must be an Ed25519 private key in PEM format")
        return SecretStr(pem)

    @field_validator("verify_keys")
    @classmethod
    def _ed25519_public_keys(cls, value: list[str]) -> list[str]:
        for pem in value:
            try:
                key = load_pem_public_key(pem.encode())
            except ValueError:
                raise ValueError("must contain Ed25519 public keys in PEM format") from None
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError("must contain Ed25519 public keys in PEM format")
        return value

    @field_validator("seed_admin_password")
    @classmethod
    def _seed_password_length(cls, value: SecretStr | None) -> SecretStr | None:
        if (
            value is not None
            and not PASSWORD_MIN_LENGTH <= len(value.get_secret_value()) <= PASSWORD_MAX_LENGTH
        ):
            raise ValueError("must be 8 to 128 characters")
        return value

    @model_validator(mode="after")
    def _one_role(self) -> Self:
        if self.signing_key is None and self.jwks_url is None:
            raise ValueError("AUTH_SIGNING_KEY or AUTH_JWKS_URL is required")
        if self.signing_key is not None and self.jwks_url is not None:
            raise ValueError("AUTH_SIGNING_KEY and AUTH_JWKS_URL must not both be set")
        if self.jwks_url is not None and self.verify_keys:
            raise ValueError("AUTH_VERIFY_KEYS is only for the issuer")
        return self

    @property
    def role(self) -> AuthRole:
        return AuthRole.ISSUER if self.signing_key is not None else AuthRole.VERIFIER

    @property
    def is_issuer(self) -> bool:
        return self.signing_key is not None

    def signing_pem(self) -> str:
        if self.signing_key is None:
            raise RuntimeError("AUTH_SIGNING_KEY is not set")
        return self.signing_key.get_secret_value()

    @field_validator("access_ttl", "refresh_ttl")
    @classmethod
    def _positive(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            raise ValueError("must be greater than 0")
        return value
