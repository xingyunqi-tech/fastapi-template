from ipaddress import ip_network
lazy from pathlib import Path

import pytest

from app.config import Environment, MetricsExporter, Quota, Settings, SettingsError
from tests.conftest import use_production


def test_app_defaults() -> None:
    app = Settings.load().app

    assert app.env is Environment.DEVELOPMENT
    assert app.port == 3000
    assert app.frontend_url is None
    assert app.cors_origins == []
    assert app.default_locale == "zh"
    assert app.trusted_proxies == 0
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
    monkeypatch.setenv("TRUSTED_PROXIES", "")
    monkeypatch.setenv("PORT", "4100")

    app = Settings.load().app

    assert app.port == 4100
    assert app.trusted_proxies == 0


def test_variable_names_are_case_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("app_port", "1234")

    assert Settings.load().app.port == 3000


def test_reads_dotenv_in_working_directory(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("APP_PORT=3100\n", encoding="utf-8")

    assert Settings.load().app.port == 3100


def test_reports_all_invalid_values_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_PORT", "70000")
    monkeypatch.setenv("TRUSTED_PROXIES", "-1")

    with pytest.raises(SettingsError) as exc_info:
        Settings.load()

    fields = {error.split(":")[0] for error in exc_info.value.errors}
    assert fields == {"APP_ENV", "APP_PORT", "TRUSTED_PROXIES"}


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


def test_production_requires_platform_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    use_production(monkeypatch)
    for name in ("REDIS_URL", "CORS_ORIGINS", "METRICS_TOKEN", "STORAGE_URL_SECRET"):
        monkeypatch.delenv(name)

    with pytest.raises(SettingsError) as exc_info:
        Settings.load()

    assert {error.split(":")[0] for error in exc_info.value.errors} == {
        "REDIS_URL",
        "CORS_ORIGINS",
        "METRICS_TOKEN",
        "STORAGE_URL_SECRET",
    }


def test_service_name_and_storage_driver_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_SERVICE_NAME")
    with pytest.raises(SettingsError, match="OTEL_SERVICE_NAME"):
        Settings.load()

    monkeypatch.setenv("OTEL_SERVICE_NAME", "api")
    monkeypatch.delenv("STORAGE_DRIVER")
    with pytest.raises(SettingsError, match="STORAGE_DRIVER"):
        Settings.load()


@pytest.mark.parametrize(
    ("exporter", "workers", "valid"),
    [
        ("prometheus", "1", True),
        ("prometheus", "2", False),
        ("otlp", "4", True),
        ("none", "2", True),
    ],
)
def test_web_concurrency_with_metrics_exporter(
    monkeypatch: pytest.MonkeyPatch, exporter: str, workers: str, valid: bool
) -> None:
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", exporter)
    monkeypatch.setenv("WEB_CONCURRENCY", workers)

    if valid:
        assert Settings.load().app.web_concurrency == int(workers)
    else:
        with pytest.raises(SettingsError, match="WEB_CONCURRENCY"):
            Settings.load()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2", 2),
        ("10.0.0.0/8, fd00::/8", (ip_network("10.0.0.0/8"), ip_network("fd00::/8"))),
        ("192.0.2.1", (ip_network("192.0.2.1/32"),)),
    ],
)
def test_trusted_proxies_accepts_hops_or_cidr(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: object
) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", value)

    assert Settings.load().app.trusted_proxies == expected


@pytest.mark.parametrize(
    ("value", "quota"),
    [
        ("10/s,burst=20", Quota(10, 1000, 20)),
        ("5 / 15m, burst=5", None),
        ("3/h,burst=2", Quota(3, 3_600_000, 2)),
    ],
)
def test_rate_limit_default(
    monkeypatch: pytest.MonkeyPatch, value: str, quota: Quota | None
) -> None:
    monkeypatch.setenv("RATE_LIMIT_DEFAULT", value)

    if quota is None:
        with pytest.raises(SettingsError, match="RATE_LIMIT_DEFAULT"):
            Settings.load()
    else:
        assert Settings.load().rate_limit.default == quota


def test_telemetry_defaults() -> None:
    telemetry = Settings.load().telemetry

    assert telemetry.service_name == "api"
    assert telemetry.metrics_exporter is MetricsExporter.PROMETHEUS
    assert telemetry.prometheus_enabled is True
    assert telemetry.traces_enabled is False


def test_traces_follow_otlp_endpoint_and_sdk_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    assert Settings.load().telemetry.traces_enabled is True

    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    telemetry = Settings.load().telemetry
    assert (telemetry.traces_enabled, telemetry.metrics_enabled) == (False, False)


def test_redis_stream_events_require_stream_and_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVENTS_TRANSPORT", "redis_stream")
    with pytest.raises(SettingsError, match="EVENTS_REDIS_STREAM"):
        Settings.load()

    monkeypatch.setenv("EVENTS_REDIS_STREAM", "events")
    with pytest.raises(SettingsError, match="REDIS_URL"):
        Settings.load()
