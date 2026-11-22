import re
from datetime import timedelta
from typing import Annotated, Any, Final

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from pydantic import BeforeValidator, Field, SecretStr, field_validator
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


class AuthSettings(SettingsGroup):
    issuer: str = Field(min_length=1, validation_alias="AUTH_ISSUER")
    audience: str = Field(min_length=1, validation_alias="AUTH_AUDIENCE")
    signing_key: SecretStr = Field(validation_alias="AUTH_SIGNING_KEY")
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
    def _ed25519_private_key(cls, value: SecretStr) -> SecretStr:
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

    @field_validator("access_ttl", "refresh_ttl")
    @classmethod
    def _positive(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            raise ValueError("must be greater than 0")
        return value
