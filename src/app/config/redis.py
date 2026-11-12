from urllib.parse import quote

from pydantic import Field, SecretStr

from app.config.base import EnvFlag, SettingsGroup


class RedisSettings(SettingsGroup):
    enabled: EnvFlag = Field(default=False, validation_alias="REDIS_ENABLED")
    host: str = Field(default="localhost", validation_alias="REDIS_HOST")
    port: int = Field(default=6379, validation_alias="REDIS_PORT")
    password: SecretStr | None = Field(default=None, validation_alias="REDIS_PASSWORD")
    db: int = Field(default=0, ge=0, validation_alias="REDIS_DB")

    def url(self) -> str:
        auth = f":{quote(self.password.get_secret_value(), safe='')}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"
