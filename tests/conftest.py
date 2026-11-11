import os
from pathlib import Path
lazy from collections.abc import Iterator

import pytest

from app.config import AppSettings, get_settings
from app.config.base import env_names

TESTS_ROOT = Path(__file__).parent
DIRECTORY_MARKERS = {"unit", "integration", "e2e", "contract", "parity"}
SETTINGS_ENV_VARS = env_names(AppSettings)


def pytest_configure(config: pytest.Config) -> None:
    for name in SETTINGS_ENV_VARS:
        os.environ.pop(name, None)


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
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
