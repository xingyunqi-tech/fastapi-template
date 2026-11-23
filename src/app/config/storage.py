from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator

from app.config.base import Duration, EnvFlag, SettingsGroup


class StorageDriver(StrEnum):
    LOCAL = "local"
    S3 = "s3"


class StorageSettings(SettingsGroup):
    """`local` 由 `getFileContent` 输出签名地址；`s3` 输出预签名 GET。"""

    driver: StorageDriver | None = Field(default=None, validation_alias="STORAGE_DRIVER")
    local_dir: Path = Field(default=Path("./var/files"), validation_alias="STORAGE_LOCAL_DIR")
    url_ttl: Duration = Field(default=timedelta(minutes=10), validation_alias="STORAGE_URL_TTL")
    url_secret: SecretStr | None = Field(default=None, validation_alias="STORAGE_URL_SECRET")
    s3_bucket: str | None = Field(default=None, validation_alias="S3_BUCKET")
    s3_region: str | None = Field(default=None, validation_alias="S3_REGION")
    s3_endpoint: str | None = Field(default=None, validation_alias="S3_ENDPOINT")
    s3_access_key_id: str | None = Field(default=None, validation_alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: SecretStr | None = Field(
        default=None, validation_alias="S3_SECRET_ACCESS_KEY"
    )
    s3_force_path_style: EnvFlag = Field(default=False, validation_alias="S3_FORCE_PATH_STYLE")

    @field_validator("url_ttl")
    @classmethod
    def _positive(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            raise ValueError("must be greater than 0")
        return value

    @model_validator(mode="after")
    def _require_s3(self) -> Self:
        if self.driver is not StorageDriver.S3:
            return self
        missing = [
            name
            for name, value in (("S3_BUCKET", self.s3_bucket), ("S3_REGION", self.s3_region))
            if not value
        ]
        if missing:
            raise ValueError(f"{', '.join(missing)}: required when STORAGE_DRIVER=s3")
        return self
