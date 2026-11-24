import os
from pathlib import Path
lazy from collections.abc import Iterator

import pytest

from app.config import (
    AlipaySettings,
    AppSettings,
    AuthSettings,
    DatabaseSettings,
    EventsSettings,
    LogSettings,
    MailSettings,
    RateLimitSettings,
    RedisSettings,
    SmsSettings,
    StorageSettings,
    TelemetrySettings,
    WechatSettings,
    get_settings,
)
from app.config.base import env_names
from tests import keys

TESTS_ROOT = Path(__file__).parent
DIRECTORY_MARKERS = {"unit", "conventions", "integration", "api", "e2e"}
SETTINGS_ENV_VARS = set().union(
    *(
        env_names(group)
        for group in (
            AppSettings,
            AuthSettings,
            DatabaseSettings,
            RedisSettings,
            MailSettings,
            RateLimitSettings,
            LogSettings,
            TelemetrySettings,
            EventsSettings,
            StorageSettings,
            SmsSettings,
            WechatSettings,
            AlipaySettings,
        )
    )
)
TEST_ENV = {
    "AUTH_ISSUER": keys.ISSUER,
    "AUTH_AUDIENCE": keys.AUDIENCE,
    "AUTH_SIGNING_KEY": keys.SIGNING_KEY,
    "OTEL_SERVICE_NAME": "api",
    "STORAGE_DRIVER": "local",
    "RATE_LIMIT_DEFAULT": "1/ms,burst=100000000",
    "DATABASE_URL": "postgresql://root:secret@localhost:5432/api?sslmode=disable",
}


def pytest_configure(config: pytest.Config) -> None:
    for name in SETTINGS_ENV_VARS:
        os.environ.pop(name, None)
    os.environ.update(TEST_ENV)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        directory = item.path.relative_to(TESTS_ROOT).parts[0]
        if directory in DIRECTORY_MARKERS:
            item.add_marker(directory)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """隔离进程环境变量与工作目录下的 .env。"""
    for name in SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in TEST_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def use_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("METRICS_TOKEN", "test_metrics_token_0123456789")
    monkeypatch.setenv("STORAGE_URL_SECRET", "test_storage_url_secret")
    monkeypatch.setenv("MAIL_HOST", "smtp.example.com")
    monkeypatch.setenv("MAIL_FROM", "Api <noreply@example.com>")
    monkeypatch.setenv("APP_FRONTEND_URL", "https://app.example.com")


JWKS_URL = "http://identity.test/.well-known/jwks.json"


def use_verifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证方的最小配置：只有 JWT 相关变量，无数据库与存储。"""
    for name in ("AUTH_SIGNING_KEY", "DATABASE_URL", "STORAGE_DRIVER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTH_JWKS_URL", JWKS_URL)
