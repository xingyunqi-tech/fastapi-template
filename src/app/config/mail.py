from enum import StrEnum

from pydantic import Field, SecretStr

from app.config.base import SettingsGroup


class MailTls(StrEnum):
    NONE = "none"
    STARTTLS = "starttls"
    TLS = "tls"


class MailSettings(SettingsGroup):
    host: str | None = Field(default=None, validation_alias="MAIL_HOST")
    port: int = Field(default=587, ge=1, le=65535, validation_alias="MAIL_PORT")
    user: str | None = Field(default=None, validation_alias="MAIL_USER")
    password: SecretStr | None = Field(default=None, validation_alias="MAIL_PASSWORD")
    sender: str | None = Field(default=None, validation_alias="MAIL_FROM")
    tls: MailTls = Field(default=MailTls.STARTTLS, validation_alias="MAIL_TLS")

    def validate_for_production(self) -> list[str]:
        required = {"MAIL_HOST": self.host, "MAIL_FROM": self.sender}
        return [f"{name}: required in production" for name, value in required.items() if not value]
