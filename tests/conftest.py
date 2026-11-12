import os
from pathlib import Path
lazy from collections.abc import Iterator

import pytest

from app.config import (
    AlipaySettings,
    AppSettings,
    AuthSettings,
    DatabaseSettings,
    FileSettings,
    LogSettings,
    MailSettings,
    MetricsSettings,
    ObservabilitySettings,
    RedisSettings,
    SmsSettings,
    ThrottlerSettings,
    WechatSettings,
    get_settings,
)
from app.config.base import env_names

TESTS_ROOT = Path(__file__).parent
DIRECTORY_MARKERS = {"unit", "integration", "e2e", "contract", "parity"}
SETTINGS_ENV_VARS = set().union(
    *(
        env_names(group)
        for group in (
            AppSettings,
            AuthSettings,
            DatabaseSettings,
            RedisSettings,
            MailSettings,
            ThrottlerSettings,
            MetricsSettings,
            LogSettings,
            ObservabilitySettings,
            FileSettings,
            SmsSettings,
            WechatSettings,
            AlipaySettings,
        )
    )
)
TEST_ENV = {
    "AUTH_JWT_SECRET": "test-access-secret-0123456789abcdef",
    "AUTH_JWT_TOKEN_EXPIRES_IN": "15m",
    "AUTH_REFRESH_SECRET": "test-refresh-secret-0123456789abcdef",
    "AUTH_REFRESH_TOKEN_EXPIRES_IN": "3650d",
    "AUTH_FORGOT_SECRET": "test-forgot-secret-0123456789abcdef",
    "AUTH_FORGOT_TOKEN_EXPIRES_IN": "30m",
    "AUTH_CONFIRM_EMAIL_SECRET": "test-confirm-secret-0123456789abcdef",
    "AUTH_CONFIRM_EMAIL_TOKEN_EXPIRES_IN": "1d",
    "THROTTLE_ENABLED": "false",
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
    monkeypatch.setenv("REDIS_ENABLED", "true")
    monkeypatch.setenv("MAIL_HOST", "smtp.example.com")
    monkeypatch.setenv("MAIL_DEFAULT_EMAIL", "noreply@example.com")
    monkeypatch.setenv("MAIL_DEFAULT_NAME", "Api")
