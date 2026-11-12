from pathlib import Path

import pytest

from app.config import Environment, Settings, SettingsError
from tests.conftest import use_production

REPO_BASELINE = Path(__file__).resolve().parents[2] / "openapi" / "nestjs-baseline.json"


def test_defaults_match_nestjs() -> None:
    app = Settings.load().app

    assert app.env is Environment.DEVELOPMENT
    assert app.port == 3000
    assert app.api_prefix == "api"
    assert app.name == "app"
    assert app.frontend_domain is None
    assert app.backend_domain == "http://localhost"
    assert app.fallback_language == "en"
    assert app.trust_proxy == 0
    assert app.swagger_enabled is True
    assert app.web_concurrency == 1
    assert app.openapi_baseline_path == REPO_BASELINE


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
def test_swagger_enabled(
    monkeypatch: pytest.MonkeyPatch, env: str, flag: str | None, expected: bool
) -> None:
    if env == "production":
        use_production(monkeypatch)
    else:
        monkeypatch.setenv("APP_ENV", env)
    if flag is not None:
        monkeypatch.setenv("SWAGGER_ENABLED", flag)

    assert Settings.load().app.swagger_enabled is expected


@pytest.mark.parametrize("flag", ["1", "TRUE", "yes", "False"])
def test_rejects_non_boolean_flag(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    monkeypatch.setenv("SWAGGER_ENABLED", flag)

    with pytest.raises(SettingsError, match="SWAGGER_ENABLED"):
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
    monkeypatch.delenv("MAIL_DEFAULT_NAME")

    with pytest.raises(SettingsError) as exc_info:
        Settings.load()

    assert {error.split(":")[0] for error in exc_info.value.errors} >= {
        "MAIL_HOST",
        "MAIL_DEFAULT_NAME",
    }
