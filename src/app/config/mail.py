from pydantic import Field, SecretStr

from app.config.base import EnvFlag, SettingsGroup


class MailSettings(SettingsGroup):
    host: str | None = Field(default=None, validation_alias="MAIL_HOST")
    port: int = Field(default=587, validation_alias="MAIL_PORT")
    user: str | None = Field(default=None, validation_alias="MAIL_USER")
    password: SecretStr | None = Field(default=None, validation_alias="MAIL_PASSWORD")
    ignore_tls: EnvFlag = Field(default=False, validation_alias="MAIL_IGNORE_TLS")
    secure: EnvFlag = Field(default=False, validation_alias="MAIL_SECURE")
    require_tls: EnvFlag = Field(default=False, validation_alias="MAIL_REQUIRE_TLS")
    default_email: str | None = Field(default=None, validation_alias="MAIL_DEFAULT_EMAIL")
    default_name: str | None = Field(default=None, validation_alias="MAIL_DEFAULT_NAME")

    def validate_for_production(self) -> list[str]:
        """生产环境与 nestjs 一样要求 MAIL_HOST、MAIL_DEFAULT_EMAIL、MAIL_DEFAULT_NAME。"""
        required = {
            "MAIL_HOST": self.host,
            "MAIL_DEFAULT_EMAIL": self.default_email,
            "MAIL_DEFAULT_NAME": self.default_name,
        }
        return [f"{name}: required in production" for name, value in required.items() if not value]
