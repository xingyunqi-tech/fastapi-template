from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import AliasChoices, Field, model_validator

from app.config.base import EnvFlag, SettingsGroup

DEFAULT_OPENAPI_BASELINE_PATH = (
    Path(__file__).resolve().parents[3] / "openapi" / "nestjs-baseline.json"
)


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class AppSettings(SettingsGroup):
    env: Environment = Field(default=Environment.DEVELOPMENT, validation_alias="APP_ENV")
    name: str = Field(default="app", validation_alias="APP_NAME")
    port: int = Field(
        default=3000, ge=0, le=65535, validation_alias=AliasChoices("APP_PORT", "PORT")
    )
    api_prefix: str = Field(default="api", validation_alias="API_PREFIX")
    frontend_domain: str | None = Field(default=None, validation_alias="FRONTEND_DOMAIN")
    backend_domain: str = Field(default="http://localhost", validation_alias="BACKEND_DOMAIN")
    fallback_language: str = Field(default="en", validation_alias="APP_FALLBACK_LANGUAGE")
    trust_proxy: int = Field(default=0, ge=0, validation_alias="TRUST_PROXY")
    swagger_enabled: EnvFlag | None = Field(default=None, validation_alias="SWAGGER_ENABLED")
    openapi_baseline_path: Path = Field(
        default=DEFAULT_OPENAPI_BASELINE_PATH, validation_alias="OPENAPI_BASELINE_PATH"
    )
    web_concurrency: int = Field(default=1, ge=1, validation_alias="WEB_CONCURRENCY")

    @model_validator(mode="after")
    def _default_swagger(self) -> Self:
        if self.swagger_enabled is None:
            self.swagger_enabled = self.env is not Environment.PRODUCTION
        return self

    @property
    def is_production(self) -> bool:
        return self.env is Environment.PRODUCTION
