from enum import StrEnum

from pydantic import Field, SecretStr

from app.config.base import SettingsGroup

PRODUCTION_SECRET_MIN_LENGTH = 32


class PasswordHasher(StrEnum):
    ARGON2ID = "argon2id"
    BCRYPT = "bcrypt"


class AuthSettings(SettingsGroup):
    secret: SecretStr = Field(validation_alias="AUTH_JWT_SECRET")
    expires: str = Field(validation_alias="AUTH_JWT_TOKEN_EXPIRES_IN")
    refresh_secret: SecretStr = Field(validation_alias="AUTH_REFRESH_SECRET")
    refresh_expires: str = Field(validation_alias="AUTH_REFRESH_TOKEN_EXPIRES_IN")
    forgot_secret: SecretStr = Field(validation_alias="AUTH_FORGOT_SECRET")
    forgot_expires: str = Field(validation_alias="AUTH_FORGOT_TOKEN_EXPIRES_IN")
    confirm_email_secret: SecretStr = Field(validation_alias="AUTH_CONFIRM_EMAIL_SECRET")
    confirm_email_expires: str = Field(validation_alias="AUTH_CONFIRM_EMAIL_TOKEN_EXPIRES_IN")
    password_hasher: PasswordHasher = Field(
        default=PasswordHasher.ARGON2ID, validation_alias="PASSWORD_HASHER"
    )

    def secrets(self) -> list[SecretStr]:
        return [self.secret, self.refresh_secret, self.forgot_secret, self.confirm_email_secret]

    def validate_for_production(self) -> list[str]:
        """生产环境要求四个密钥均 ≥32 字符且互不相同。"""
        names = [
            "AUTH_JWT_SECRET",
            "AUTH_REFRESH_SECRET",
            "AUTH_FORGOT_SECRET",
            "AUTH_CONFIRM_EMAIL_SECRET",
        ]
        values = [s.get_secret_value() for s in self.secrets()]
        errors = [
            f"{name}: must be at least {PRODUCTION_SECRET_MIN_LENGTH} characters in production"
            for name, value in zip(names, values, strict=True)
            if len(value) < PRODUCTION_SECRET_MIN_LENGTH
        ]
        if len(set(values)) != len(values):
            errors.append(f"{', '.join(names)}: must be different from each other in production")
        return errors
