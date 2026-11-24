import importlib
import ssl
import sys
from datetime import timedelta

import certifi
import pytest
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.config import Settings, SettingsError, get_settings
from app.config.database import DatabaseSettings, _ssl_context
from app.factory import build_context
from app.infrastructure.database.session import Database

URL = "postgresql://app:p%40ss@db.internal:6543/app"


def settings(**env: str) -> DatabaseSettings:
    return DatabaseSettings.model_validate({"DATABASE_URL": URL, **env})


def _pool(database: Database) -> AsyncAdaptedQueuePool:
    assert database.engine is not None
    pool = database.engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    return pool


def test_libpq_uri_becomes_asyncpg_url() -> None:
    url = settings(DATABASE_URL=f"{URL}?sslmode=require&application_name=api").sqlalchemy_url()

    assert url.drivername == "postgresql+asyncpg"
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "app",
        "p@ss",
        "db.internal",
        6543,
        "app",
    )
    assert dict(url.query) == {}


def test_connect_args_carry_schema_timezone_and_sslmode() -> None:
    args = settings(
        DATABASE_URL=f"{URL}?sslmode=disable&application_name=api", DATABASE_SCHEMA="notes"
    ).connect_args()

    assert args["server_settings"] == {
        "timezone": "UTC",
        "search_path": "notes",
        "application_name": "api",
    }
    assert args["ssl"] == "disable"
    assert args["timeout"] == 10


def test_sslmode_defaults_to_prefer() -> None:
    assert settings().connect_args()["ssl"] == "prefer"


def test_require_without_root_cert_skips_verification() -> None:
    context = _ssl_context("require", {})

    assert (context.verify_mode, context.check_hostname) == (ssl.CERT_NONE, False)


def test_require_without_files_passes_sslmode() -> None:
    assert settings(DATABASE_URL=f"{URL}?sslmode=require").connect_args()["ssl"] == "require"


@pytest.mark.parametrize(
    ("mode", "check_hostname"), [("verify-full", True), ("verify-ca", False), ("require", False)]
)
def test_root_cert_enables_certificate_verification(mode: str, check_hostname: bool) -> None:
    url = f"{URL}?sslmode={mode}&sslrootcert={certifi.where()}"
    context = settings(DATABASE_URL=url).connect_args()["ssl"]

    assert isinstance(context, ssl.SSLContext)
    assert (context.verify_mode, context.check_hostname) == (ssl.CERT_REQUIRED, check_hostname)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://app@db/app",
        "mysql://app@db/app",
        f"{URL}?sslmode=strict",
        f"{URL}?connect_timeout=5",
    ],
)
def test_rejects_non_libpq_urls(url: str) -> None:
    with pytest.raises(ValueError, match=r"libpq|sslmode|unsupported"):
        settings(DATABASE_URL=url)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        (f"{URL}?sslmode=verify-ca", "requires sslrootcert"),
        (f"{URL}?sslmode=verify-full&sslcert={certifi.where()}", "requires sslrootcert"),
        (f"{URL}?sslmode=prefer&sslrootcert={certifi.where()}", "certificate files require"),
        (f"{URL}?sslrootcert={certifi.where()}", "certificate files require"),
        (f"{URL}?sslmode=require&sslkey={certifi.where()}", "sslkey requires sslcert"),
        (f"{URL}?sslmode=verify-ca&sslrootcert=/missing/root.crt", "not found: sslrootcert"),
        (f"{URL}?sslmode=verify-ca&sslrootcert=system", "not found: sslrootcert"),
    ],
)
def test_rejects_inconsistent_ssl_parameters(url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        settings(DATABASE_URL=url)


@pytest.mark.parametrize(
    "url", ["postgresql://app@h1:5432,h2:5433/app", "postgresql://app@h1,h2/app"]
)
def test_rejects_multiple_hosts(url: str) -> None:
    with pytest.raises(ValueError, match="multiple hosts"):
        settings(DATABASE_URL=url)


def test_rejects_repeated_parameters() -> None:
    with pytest.raises(ValueError, match="repeated parameters: application_name"):
        settings(DATABASE_URL=f"{URL}?application_name=a&application_name=b")


@pytest.mark.parametrize("schema", ["Notes", "1notes", "notes-api", 'a"b', "x" * 64])
def test_rejects_invalid_schema_names(schema: str) -> None:
    with pytest.raises(ValueError, match=r"\[a-z_\]"):
        settings(DATABASE_SCHEMA=schema)


@pytest.mark.parametrize("schema", ["pg_catalog", "pg_temp", "information_schema"])
def test_rejects_reserved_schema_names(schema: str) -> None:
    with pytest.raises(ValueError, match="reserved"):
        settings(DATABASE_SCHEMA=schema)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10s", timedelta(seconds=10)),
        ("1m30s", timedelta(seconds=90)),
        ("500ms", timedelta(milliseconds=500)),
    ],
)
def test_pool_timeout_uses_duration_format(value: str, expected: timedelta) -> None:
    assert settings(DATABASE_POOL_TIMEOUT=value).pool_timeout == expected


@pytest.mark.parametrize("value", ["10", "10 s", "1d", "s", "-1s"])
def test_pool_timeout_rejects_other_formats(value: str) -> None:
    with pytest.raises(ValueError, match="duration"):
        settings(DATABASE_POOL_TIMEOUT=value)


@pytest.mark.parametrize("value", ["0s", "0ms", "0h0m"])
def test_pool_timeout_must_be_positive(value: str) -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        settings(DATABASE_POOL_TIMEOUT=value)


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(SettingsError, match="DATABASE_URL"):
        Settings.load()


async def test_pool_follows_pool_size_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_POOL_SIZE", "4")
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT", "2500ms")
    database = Database.from_settings(Settings.load().database)

    pool = _pool(database)
    assert (pool.size(), pool._max_overflow, pool._timeout) == (4, 0, 2.5)
    await database.dispose()


async def test_pool_size_is_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "otlp")
    context = build_context(Settings.load())

    assert _pool(context.db).size() == 10
    await context.db.dispose()


async def test_explicit_pool_size_overrides_setting() -> None:
    context = build_context(Settings.load(), pool_size=1)

    assert _pool(context.db).size() == 1
    await context.db.dispose()


async def test_worker_process_uses_database_pool_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    monkeypatch.setenv("DATABASE_POOL_SIZE", "3")
    get_settings.cache_clear()
    monkeypatch.delitem(sys.modules, "app.infrastructure.queue.worker", raising=False)
    worker = importlib.import_module("app.infrastructure.queue.worker")

    assert _pool(worker.context.database).size() == 3
    await worker.context.database.dispose()
    await worker.context.redis.aclose()
