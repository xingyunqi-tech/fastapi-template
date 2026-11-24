import re
import ssl
from datetime import timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from sqlalchemy.engine import URL, make_url

from app.config.base import Duration, SettingsGroup

_SCHEMA_RE: Final = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_SSL_MODES: Final = frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"})
_SSL_FILES: Final = ("sslrootcert", "sslcert", "sslkey")
_SUPPORTED_PARAMS: Final = frozenset({"sslmode", *_SSL_FILES, "application_name"})


class DatabaseSettings(SettingsGroup):
    url: SecretStr | None = Field(default=None, validation_alias="DATABASE_URL")
    db_schema: str = Field(default="public", validation_alias="DATABASE_SCHEMA")
    pool_size: int = Field(default=10, ge=1, validation_alias="DATABASE_POOL_SIZE")
    pool_timeout: Duration = Field(
        default=timedelta(seconds=10), validation_alias="DATABASE_POOL_TIMEOUT"
    )

    @field_validator("url")
    @classmethod
    def _libpq_uri(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value()
        if "," in urlsplit(raw).netloc:
            raise ValueError("multiple hosts are not supported")
        url = make_url(raw)
        if url.drivername not in {"postgresql", "postgres"}:
            raise ValueError("must be a libpq URI starting with postgresql://")
        unsupported = sorted(set(url.query) - _SUPPORTED_PARAMS)
        if unsupported:
            raise ValueError(f"unsupported parameters: {', '.join(unsupported)}")
        repeated = sorted(key for key, item in url.query.items() if not isinstance(item, str))
        if repeated:
            raise ValueError(f"repeated parameters: {', '.join(repeated)}")
        mode = url.query.get("sslmode", "prefer")
        if mode not in _SSL_MODES:
            raise ValueError(f"unsupported sslmode: {mode}")
        files = {key: str(url.query[key]) for key in _SSL_FILES if key in url.query}
        if files and mode not in {"require", "verify-ca", "verify-full"}:
            raise ValueError("certificate files require sslmode require, verify-ca or verify-full")
        if mode in {"verify-ca", "verify-full"} and "sslrootcert" not in files:
            raise ValueError(f"sslmode {mode} requires sslrootcert")
        if "sslkey" in files and "sslcert" not in files:
            raise ValueError("sslkey requires sslcert")
        missing = sorted(key for key, path in files.items() if not Path(path).is_file())
        if missing:
            raise ValueError(f"certificate files not found: {', '.join(missing)}")
        return value

    @field_validator("db_schema")
    @classmethod
    def _identifier(cls, value: str) -> str:
        if not _SCHEMA_RE.fullmatch(value):
            raise ValueError("must match [a-z_][a-z0-9_]{0,62}")
        if value.startswith("pg_") or value == "information_schema":
            raise ValueError("must not be a reserved schema name")
        return value

    @field_validator("pool_timeout")
    @classmethod
    def _positive(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            raise ValueError("must be greater than 0")
        return value

    @property
    def enabled(self) -> bool:
        return self.url is not None

    def _libpq(self) -> URL:
        if self.url is None:
            raise RuntimeError("DATABASE_URL is not set")
        return make_url(self.url.get_secret_value())

    def sqlalchemy_url(self) -> URL:
        """asyncpg 驱动 URL，libpq 查询参数由 `connect_args` 承接。"""
        return self._libpq().set(drivername="postgresql+asyncpg", query={})

    def connect_args(self) -> dict[str, Any]:
        """asyncpg 连接参数：`search_path` 为 `DATABASE_SCHEMA`，时区 UTC，`sslmode` 转为 `ssl`。"""
        query = self._libpq().query
        server_settings = {"timezone": "UTC", "search_path": self.db_schema}
        if name := query.get("application_name"):
            server_settings["application_name"] = str(name)
        args: dict[str, Any] = {
            "server_settings": server_settings,
            "timeout": self.pool_timeout.total_seconds(),
        }
        mode = str(query.get("sslmode", "prefer"))
        files = {key: str(query[key]) for key in _SSL_FILES if key in query}
        args["ssl"] = _ssl_context(mode, files) if files else mode
        return args


def _ssl_context(mode: str, files: dict[str, str]) -> ssl.SSLContext:
    """无 `sslrootcert` 时不校验证书；有则校验证书链，`verify-full` 另校验主机名。"""
    context = ssl.create_default_context(cafile=files.get("sslrootcert"))
    if "sslrootcert" not in files:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif mode != "verify-full":
        context.check_hostname = False
    if "sslcert" in files:
        context.load_cert_chain(files["sslcert"], files.get("sslkey"))
    return context
