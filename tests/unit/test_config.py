lazy from pathlib import Path

import pytest

from app.config import Environment, Settings, SettingsError
from tests.conftest import use_production


def test_app_defaults() -> None:
    app = Settings.load().app

    assert app.env is Environment.DEVELOPMENT
    assert app.port == 3000
    assert app.name == "app"
    assert app.frontend_url is None
    assert app.cors_origins == []
    assert app.backend_domain == "http://localhost"
    assert app.default_locale == "zh"
    assert app.trust_proxy == 0
    assert app.docs_enabled is True
    assert int(app.body_limit) == 1024 * 1024
    assert app.web_concurrency == 1


def test_cors_origins_and_body_limit_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example,")
    monkeypatch.setenv("BODY_LIMIT", "2MiB")
    app = Settings.load().app

    assert app.cors_origins == ["https://a.example", "https://b.example"]
    assert int(app.body_limit) == 2 * 1024 * 1024


def test_rejects_unsupported_default_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DEFAULT_LOCALE", "fr")

    with pytest.raises(SettingsError, match="APP_DEFAULT_LOCALE"):
        Settings.load()


def test_port_falls_back_to_port_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "4000")
    assert Settings.load().app.port == 4000

    monkeypatch.setenv("APP_PORT", "5000")
    assert Settings.load().app.port == 5000


@pytest.mark.parametrize(
    ("env", "flag", "expected"),
    [
        ("production", None, False),
        ("development", None, True),
        ("production", "true", True),
        ("development", "false", False),
        ("production", "", False),
        ("development", "", True),
    ],
)
def test_docs_enabled(
    monkeypatch: pytest.MonkeyPatch, env: str, flag: str | None, expected: bool
) -> None:
    if env == "production":
        use_production(monkeypatch)
    else:
        monkeypatch.setenv("APP_ENV", env)
    if flag is not None:
        monkeypatch.setenv("DOCS_ENABLED", flag)

    assert Settings.load().app.docs_enabled is expected


@pytest.mark.parametrize("flag", ["1", "TRUE", "yes", "False"])
def test_rejects_non_boolean_flag(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    monkeypatch.setenv("DOCS_ENABLED", flag)

    with pytest.raises(SettingsError, match="DOCS_ENABLED"):
        Settings.load()


def test_empty_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PORT", "")
    monkeypatch.setenv("TRUST_PROXY", "")
    monkeypatch.setenv("PORT", "4100")

    app = Settings.load().app

    assert app.port == 4100
    assert app.trust_proxy == 0


def test_variable_names_are_case_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("app_port", "1234")

    assert Settings.load().app.port == 3000


def test_reads_dotenv_in_working_directory(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_PORT=3100\n", encoding="utf-8")

    assert Settings.load().app.port == 3100


def test_reports_all_invalid_values_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_PORT", "70000")
    monkeypatch.setenv("TRUST_PROXY", "-1")

    with pytest.raises(SettingsError) as exc_info:
        Settings.load()

    fields = {error.split(":")[0] for error in exc_info.value.errors}
    assert fields == {"APP_ENV", "APP_PORT", "TRUST_PROXY"}


def test_production_requires_mail_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    use_production(monkeypatch)
    monkeypatch.delenv("MAIL_HOST")
    monkeypatch.delenv("MAIL_FROM")

    with pytest.raises(SettingsError) as exc_info:
        Settings.load()

    assert {error.split(":")[0] for error in exc_info.value.errors} >= {
        "MAIL_HOST",
        "MAIL_FROM",
    }
