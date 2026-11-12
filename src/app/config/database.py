import ssl
import tempfile
from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr
from sqlalchemy.engine import URL, make_url

from app.config.base import EnvFlag, SettingsGroup


class SchemaOwner(StrEnum):
    ALEMBIC = "alembic"
    EXTERNAL = "external"


class DatabaseSettings(SettingsGroup):
    url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    host: str = Field(default="localhost", validation_alias="DATABASE_HOST")
    port: int = Field(default=5432, validation_alias="DATABASE_PORT")
    username: str = Field(default="root", validation_alias="DATABASE_USERNAME")
    password: SecretStr = Field(default=SecretStr("secret"), validation_alias="DATABASE_PASSWORD")
    name: str = Field(default="api", validation_alias="DATABASE_NAME")
    max_connections: int = Field(default=100, ge=1, validation_alias="DATABASE_MAX_CONNECTIONS")
    ssl_enabled: EnvFlag = Field(default=False, validation_alias="DATABASE_SSL_ENABLED")
    reject_unauthorized: EnvFlag = Field(
        default=True, validation_alias="DATABASE_REJECT_UNAUTHORIZED"
    )
    ca: str | None = Field(default=None, validation_alias="DATABASE_CA")
    key: SecretStr | None = Field(default=None, validation_alias="DATABASE_KEY")
    cert: str | None = Field(default=None, validation_alias="DATABASE_CERT")
    schema_owner: SchemaOwner = Field(
        default=SchemaOwner.ALEMBIC, validation_alias="DB_SCHEMA_OWNER"
    )

    def sqlalchemy_url(self) -> URL:
        if self.url:
            return make_url(self.url).set(drivername="postgresql+asyncpg")
        return URL.create(
            "postgresql+asyncpg",
            username=self.username,
            password=self.password.get_secret_value(),
            host=self.host,
            port=self.port,
            database=self.name,
        )

    def ssl_context(self) -> ssl.SSLContext | None:
        """DATABASE_CA / KEY / CERT 为 PEM 文本，语义同 node-postgres 的 ssl 选项。"""
        if not self.ssl_enabled:
            return None
        context = ssl.create_default_context(cadata=self.ca or None)
        if not self.reject_unauthorized:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        if self.cert:
            with tempfile.TemporaryDirectory() as directory:
                cert_file = Path(directory) / "cert.pem"
                cert_file.write_text(self.cert, encoding="utf-8")
                key_file = None
                if self.key is not None:
                    key_file = Path(directory) / "key.pem"
                    key_file.write_text(self.key.get_secret_value(), encoding="utf-8")
                context.load_cert_chain(cert_file, key_file)
        return context
