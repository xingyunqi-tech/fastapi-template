import ipaddress
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import AliasChoices, BeforeValidator, ByteSize, Field, model_validator
from pydantic_settings import NoDecode

from app.config.base import EnvFlag, SettingsGroup


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


def _split_origins(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


Origins = Annotated[list[str], NoDecode, BeforeValidator(_split_origins)]

type Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _parse_proxies(value: Any) -> Any:
    """层数（`2`）或逗号分隔的 CIDR 列表（`10.0.0.0/8,fd00::/8`）。"""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    try:
        return tuple(
            ipaddress.ip_network(part.strip(), strict=False)
            for part in text.split(",")
            if part.strip()
        )
    except ValueError:
        raise ValueError("must be a hop count or a comma separated list of CIDR") from None


TrustedProxies = Annotated[int | tuple[Network, ...], NoDecode, BeforeValidator(_parse_proxies)]


class AppSettings(SettingsGroup):
    env: Environment = Field(default=Environment.DEVELOPMENT, validation_alias="APP_ENV")
    port: int = Field(
        default=3000, ge=0, le=65535, validation_alias=AliasChoices("APP_PORT", "PORT")
    )
    default_locale: str = Field(
        default="zh", pattern="^(zh|en)$", validation_alias="APP_DEFAULT_LOCALE"
    )
    frontend_url: str | None = Field(default=None, validation_alias="APP_FRONTEND_URL")
    cors_origins: Origins = Field(default_factory=list, validation_alias="CORS_ORIGINS")
    trusted_proxies: TrustedProxies = Field(default=0, validation_alias="TRUSTED_PROXIES")
    docs_enabled: EnvFlag | None = Field(default=None, validation_alias="DOCS_ENABLED")
    body_limit: ByteSize = Field(default=ByteSize(1024 * 1024), validation_alias="BODY_LIMIT")
    web_concurrency: int = Field(default=1, ge=1, validation_alias="WEB_CONCURRENCY")

    @model_validator(mode="after")
    def _default_docs(self) -> Self:
        if self.docs_enabled is None:
            self.docs_enabled = self.env is not Environment.PRODUCTION
        return self

    @property
    def is_production(self) -> bool:
        return self.env is Environment.PRODUCTION
