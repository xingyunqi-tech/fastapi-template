from enum import StrEnum
from typing import Self

from pydantic import Field, SecretStr, model_validator

from app.config.base import EnvFlag, SettingsGroup

MB = 1024 * 1024


class FileDriver(StrEnum):
    LOCAL = "local"
    S3 = "s3"
    S3_PRESIGNED = "s3-presigned"


class FileSettings(SettingsGroup):
    """nestjs file.config.ts：S3 系驱动要求凭据、桶与区域。"""

    driver: FileDriver = Field(default=FileDriver.LOCAL, validation_alias="FILE_DRIVER")
    access_key_id: str | None = Field(default=None, validation_alias="ACCESS_KEY_ID")
    secret_access_key: SecretStr | None = Field(default=None, validation_alias="SECRET_ACCESS_KEY")
    bucket: str | None = Field(default=None, validation_alias="AWS_DEFAULT_S3_BUCKET")
    region: str | None = Field(default=None, validation_alias="AWS_S3_REGION")
    endpoint: str | None = Field(default=None, validation_alias="AWS_S3_ENDPOINT")
    force_path_style: EnvFlag = Field(default=True, validation_alias="AWS_S3_FORCE_PATH_STYLE")
    image_max_size: int = Field(default=5 * MB, ge=1, validation_alias="FILE_IMAGE_MAX_SIZE")
    private_image_max_size: int = Field(
        default=10 * MB, ge=1, validation_alias="FILE_PRIVATE_IMAGE_MAX_SIZE"
    )
    content_image_max_size: int = Field(
        default=5 * MB, ge=1, validation_alias="FILE_CONTENT_IMAGE_MAX_SIZE"
    )
    audio_max_size: int = Field(default=20 * MB, ge=1, validation_alias="FILE_AUDIO_MAX_SIZE")
    import_max_size: int = Field(default=50 * MB, ge=1, validation_alias="FILE_IMPORT_MAX_SIZE")
    export_max_size: int = Field(default=20 * MB, ge=1, validation_alias="FILE_EXPORT_MAX_SIZE")

    @model_validator(mode="after")
    def _require_s3(self) -> Self:
        if self.driver is FileDriver.LOCAL:
            return self
        missing = [
            name
            for name, value in (
                ("ACCESS_KEY_ID", self.access_key_id),
                ("SECRET_ACCESS_KEY", self.secret_access_key),
                ("AWS_DEFAULT_S3_BUCKET", self.bucket),
                ("AWS_S3_REGION", self.region),
            )
            if value is None
        ]
        if missing:
            raise ValueError(f"{', '.join(missing)}: required when FILE_DRIVER={self.driver}")
        return self

    @property
    def max_sizes(self) -> dict[str, int]:
        return {
            "image": self.image_max_size,
            "private-image": self.private_image_max_size,
            "content-image": self.content_image_max_size,
            "audio": self.audio_max_size,
            "import": self.import_max_size,
            "export": self.export_max_size,
        }

    @property
    def max_file_size(self) -> int:
        return max(self.max_sizes.values())
