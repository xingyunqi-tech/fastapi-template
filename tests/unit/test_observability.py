import hashlib
import json
import logging
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
lazy from collections.abc import Iterator
lazy from pathlib import Path

import psutil  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app import __main__ as app_main
from app.common.exceptions import HttpError
from app.common.security import Jwt
from app.config import FileDriver, LogLevel, Settings, SettingsError
from app.factory import create_app
from app.infrastructure import observability
from app.infrastructure.health import indicators
from app.infrastructure.logging import configure_logging
from app.infrastructure.logging.middleware import level_for
from app.infrastructure.metrics.database import classify
from app.infrastructure.metrics.guard import monitoring_token, token_matches
from app.infrastructure.metrics.middleware import express_path
from app.infrastructure.observability import setup_otel, setup_sentry
from app.infrastructure.throttler import MemoryGcra, normalize_ip, redis_key
from app.infrastructure.throttler import storage as throttler_storage
from app.infrastructure.throttler.guard import handler_name, nest_handlers
from tests.conftest import TEST_ENV, use_production


class TestNormalizeIp:
    @pytest.mark.parametrize(
        ("ip", "expected"),
        [
            ("203.0.113.7", "203.0.113.7"),
            ("", ""),
            ("not-an-ip", "not-an-ip"),
            ("::1", "::1"),
            ("0:0:0:0:0:0:0:1", "::1"),
            ("::ffff:198.51.100.4", "198.51.100.4"),
            ("2001:db8:abcd:12:1:2:3:4", "2001:db8:abcd:12::/64"),
            ("2001:DB8::1%eth0", "2001:db8::/64"),
            ("2001:db8:0:0:1::", "2001:db8::/64"),
        ],
    )
    def test_matches_nestjs(self, ip: str, expected: str) -> None:
        assert normalize_ip(ip) == expected


class TestMemoryGcra:
    def test_allows_limit_then_blocks(self) -> None:
        gcra = MemoryGcra()
        decisions = [gcra.consume("k", 60_000, 3, now=0) for _ in range(4)]

        assert [d.allowed for d in decisions] == [True, True, True, False]
        assert [d.remaining for d in decisions[:3]] == [2, 1, 0]
        assert decisions[2].reset_seconds == 60
        assert decisions[3].retry_after_seconds == 20

    def test_replenishes_gradually(self) -> None:
        gcra = MemoryGcra()
        for _ in range(3):
            gcra.consume("k", 60_000, 3, now=0)

        assert gcra.consume("k", 60_000, 3, now=19_999).allowed is False
        later = gcra.consume("k", 60_000, 3, now=20_000)
        assert later.allowed is True
        assert later.remaining == 0

    def test_keys_are_independent(self) -> None:
        gcra = MemoryGcra()
        gcra.consume("a", 1000, 1, now=0)

        assert gcra.consume("b", 1000, 1, now=0).allowed is True
        assert gcra.consume("a", 1000, 1, now=0).allowed is False

    def test_remaining_with_fractional_interval(self) -> None:
        gcra = MemoryGcra()

        assert gcra.consume("k", 60_000, 7, now=0).remaining == 6

    def test_full_table_drops_expired_keys_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(throttler_storage, "MEMORY_MAX_KEYS", 3)
        gcra = MemoryGcra()
        gcra.consume("long", 60_000, 1, now=0)
        gcra.consume("short", 1_000, 1, now=0)
        gcra.consume("medium", 10_000, 1, now=0)

        gcra.consume("new", 60_000, 1, now=5_000)

        assert set(gcra._tat) == {"long", "medium", "new"}

    def test_full_table_evicts_earliest_expiring_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(throttler_storage, "MEMORY_MAX_KEYS", 3)
        gcra = MemoryGcra()
        gcra.consume("long", 60_000, 1, now=0)
        gcra.consume("short", 1_000, 1, now=0)
        gcra.consume("medium", 10_000, 1, now=0)

        gcra.consume("new", 60_000, 1, now=500)

        assert set(gcra._tat) == {"long", "medium", "new"}
        assert gcra.consume("long", 60_000, 1, now=500).allowed is False


class TestThrottleKeys:
    def test_handler_table_covers_auth_routes(self) -> None:
        handlers = nest_handlers()

        assert handlers["loginWithEmail"] == "AuthController-login"
        assert handlers["updateCurrentUser"] == "AuthController-update"
        assert handlers["healthCheck"] == "HealthController-check"
        assert handlers["getMetrics"] == "MetricsController-index"

    def test_unknown_operation_falls_back_to_operation_id(self) -> None:
        route = SimpleNamespace(operation_id="notInNest", name="fn")

        assert handler_name(route) == "notInNest"  # type: ignore[arg-type]

    def test_redis_key_layout(self) -> None:
        digest = hashlib.sha256(b"AuthController-login-default-ip:1.2.3.4").hexdigest()

        assert redis_key(digest) == f"throttle:{digest}:default:gcra"

    @pytest.mark.parametrize(
        ("operation_id", "driver", "expected"),
        [
            ("readPrivateFile", FileDriver.LOCAL, "FilesController-readPrivate"),
            ("readPrivateFile", FileDriver.S3, "FilesController-readPrivate"),
            ("downloadFile", FileDriver.LOCAL, "FilesLocalController-download"),
            ("uploadFile", FileDriver.LOCAL, "FilesLocalController-uploadFile"),
            ("uploadFile", FileDriver.S3, "FilesS3Controller-uploadFile"),
            ("uploadFile", FileDriver.S3_PRESIGNED, "FilesS3PresignedController-uploadFile"),
        ],
    )
    def test_files_handlers(self, operation_id: str, driver: FileDriver, expected: str) -> None:
        route = SimpleNamespace(operation_id=operation_id, name="fn")

        assert handler_name(route, driver) == expected  # type: ignore[arg-type]


class TestConfig:
    def test_defaults_match_nestjs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("THROTTLE_ENABLED")
        settings = Settings.load()

        assert settings.throttler.enabled is True
        assert settings.throttler.ttl_ms == 60_000
        assert settings.throttler.limit == 100
        assert settings.metrics.enabled is True
        assert settings.metrics.path == "metrics"
        assert settings.metrics.token is None
        assert settings.observability.sentry_dsn is None
        assert settings.observability.otel_enabled is False

    @pytest.mark.parametrize(("value", "expected"), [("false", False), ("0", True), ("", True)])
    def test_metrics_enabled_only_false_disables(
        self, monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
    ) -> None:
        monkeypatch.setenv("METRICS_ENABLED", value)

        assert Settings.load().metrics.enabled is expected

    @pytest.mark.parametrize(
        ("env", "overrides"),
        [
            ("THROTTLE_TTL", "0"),
            ("THROTTLE_LIMIT", "abc"),
            ("THROTTLE_ENABLED", "yes"),
            ("METRICS_TOKEN", "short"),
            ("LOG_LEVEL", "verbose"),
        ],
    )
    def test_rejects_invalid_values(
        self, monkeypatch: pytest.MonkeyPatch, env: str, overrides: str
    ) -> None:
        monkeypatch.setenv(env, overrides)

        with pytest.raises(SettingsError):
            Settings.load()

    def test_log_defaults_follow_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        development = Settings.load().log
        use_production(monkeypatch)
        production = Settings.load().log

        assert development.resolved_level(production=False) is LogLevel.DEBUG
        assert development.resolved_pretty(production=False) is True
        assert production.resolved_level(production=True) is LogLevel.INFO
        assert production.resolved_pretty(production=True) is False

    def test_log_pretty_print_is_true_only_for_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_PRETTY_PRINT", "yes")

        assert Settings.load().log.resolved_pretty(production=False) is False


def _monitoring_request(token: str | None, production: bool, authorization: str | None) -> Any:
    settings = SimpleNamespace(
        metrics=SimpleNamespace(
            token=None if token is None else SimpleNamespace(get_secret_value=lambda: token)
        ),
        app=SimpleNamespace(is_production=production),
    )
    headers = {} if authorization is None else {"authorization": authorization}
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(context=SimpleNamespace(settings=settings))),
        headers=headers,
    )


class TestMonitoringToken:
    async def test_without_token_only_non_production_passes(self) -> None:
        await monitoring_token(_monitoring_request(None, False, None))

        with pytest.raises(HttpError) as raised:
            await monitoring_token(_monitoring_request(None, True, None))
        assert raised.value.status == 403

    async def test_production_with_token(self) -> None:
        await monitoring_token(_monitoring_request("t" * 16, True, "Bearer " + "t" * 16))

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("Bearer secret", True),
            ("  BEARER   secret  ", True),
            ("Bearer secret extra", True),
            ("Bearer secre", False),
            ("Basic secret", False),
            ("Bearer", False),
            (None, False),
        ],
    )
    def test_token_matching(self, header: str | None, expected: bool) -> None:
        assert token_matches(header, "secret") is expected


class TestMetricsHelpers:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/api/v1/users/{id}", "/api/v1/users/:id"),
            ("/files/{path:path}", "/files/:path"),
            ("/", "/"),
        ],
    )
    def test_express_path(self, path: str, expected: str) -> None:
        assert express_path(path) == expected

    @pytest.mark.parametrize(
        ("statement", "expected"),
        [
            ('SELECT "user".id FROM "user" WHERE id = $1', ("SELECT", "user")),
            ("INSERT INTO session (hash) VALUES ($1)", ("INSERT", "session")),
            ('UPDATE public."user" SET x = 1', ("UPDATE", "user")),
            ("DELETE FROM notification", ("DELETE", "notification")),
            ("SELECT 1", ("SELECT", "unknown")),
        ],
    )
    def test_classify(self, statement: str, expected: tuple[str, str]) -> None:
        assert classify(statement) == expected


class TestLogging:
    @pytest.fixture(autouse=True)
    def restore_root_logger(self) -> Iterator[None]:
        root = logging.getLogger()
        level, handlers = root.level, list(root.handlers)
        yield
        root.setLevel(level)
        root.handlers[:] = handlers

    @pytest.mark.parametrize(
        ("status", "failed", "level"),
        [
            (200, False, "info"),
            (304, False, "info"),
            (404, False, "warning"),
            (499, False, "warning"),
            (500, False, "error"),
            (200, True, "error"),
        ],
    )
    def test_level_rules(self, status: int, failed: bool, level: str) -> None:
        assert level_for(status, failed) == level

    def test_production_json_with_request_context(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LOG_PRETTY_PRINT", "false")
        app = create_app(Settings.load())
        token = Jwt.sign({"id": 7, "sessionId": 1}, TEST_ENV["AUTH_JWT_SECRET"], "15m")
        capsys.readouterr()

        response = TestClient(app).get(
            "/?token=abc&page=1",
            headers={"Authorization": f"Bearer {token}", "Cookie": "a=b", "X-Request-Id": "rid-1"},
        )

        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        [access] = [line for line in lines if line.get("logger") == "app.http.access"]
        assert response.status_code == 200
        assert access["msg"] == "GET /?token=abc&page=1 200"
        assert access["level"] == "info"
        assert access["request_id"] == "rid-1"
        assert access["user_id"] == "7"
        assert access["context"] == "HTTP"
        assert access["res"] == {"statusCode": 200}
        assert access["req"]["headers"]["authorization"] == "[REDACTED]"
        assert access["req"]["headers"]["cookie"] == "[REDACTED]"
        assert access["req"]["query"] == {"token": "[REDACTED]", "page": "1"}
        assert isinstance(access["responseTime"], int)

    def test_access_log_uses_resolved_request_id(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LOG_PRETTY_PRINT", "false")
        app = create_app(Settings.load())
        capsys.readouterr()

        response = TestClient(app).get("/", headers={"X-Request-Id": "bad id!"})

        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        [access] = [line for line in lines if line.get("logger") == "app.http.access"]
        assert access["req"]["id"] == response.headers["x-request-id"] != "bad id!"

    def test_http_client_request_lines_are_not_logged(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "info")
        monkeypatch.setenv("LOG_PRETTY_PRINT", "false")
        configure_logging(Settings.load())
        capsys.readouterr()

        logging.getLogger("httpx").info(
            "HTTP Request: GET https://api.weixin.qq.com/sns/jscode2session?secret=TOPSECRET"
        )
        logging.getLogger("httpx").warning("httpx warning")

        out = capsys.readouterr().out
        assert "TOPSECRET" not in out
        assert "httpx warning" in out

    def test_stdlib_logging_goes_through_structlog(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LOG_PRETTY_PRINT", "false")
        configure_logging(Settings.load())
        capsys.readouterr()

        logging.getLogger("app.test").warning("hello %s", "world", extra={"path": "/x"})

        [line] = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x]
        assert line["msg"] == "hello world"
        assert line["level"] == "warning"
        assert line["logger"] == "app.test"
        assert line["path"] == "/x"

    def test_warn_and_error_status_levels(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LOG_PRETTY_PRINT", "false")
        client = TestClient(create_app(Settings.load()))
        capsys.readouterr()

        client.get("/api/v1/nope")

        lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x]
        [access] = [line for line in lines if line.get("logger") == "app.http.access"]
        assert access["level"] == "warning"
        assert access["msg"] == "GET /api/v1/nope 404"

    def test_silent_level_suppresses_output(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "silent")
        configure_logging(Settings.load())
        capsys.readouterr()

        logging.getLogger("app.test").critical("boom")

        assert capsys.readouterr().out == ""


class TestMemoryHeap:
    def test_threshold_is_300mb(self) -> None:
        assert indicators.HEAP_THRESHOLD_BYTES == 300 * 1024 * 1024

    @pytest.mark.parametrize(
        ("rss", "status"), [(300 * 1024 * 1024, "up"), (300 * 1024 * 1024 + 1, "down")]
    )
    async def test_compares_rss_with_threshold(
        self, monkeypatch: pytest.MonkeyPatch, rss: int, status: str
    ) -> None:
        process = SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=rss))
        monkeypatch.setattr(psutil, "Process", lambda: process)

        result = await indicators.memory_heap("memory_heap")

        assert result["memory_heap"]["status"] == status


class TestMultiprocess:
    def test_render_aggregates_worker_files(self, tmp_path: Path) -> None:
        script = (
            "from app.infrastructure import metrics\n"
            "metrics.record_cache_hit('cache')\n"
            "metrics.http_active_connections.inc()\n"
            "print(metrics.render('Multi', include_default=True).decode())\n"
        )
        env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path)}
        for _ in range(2):
            subprocess.run([sys.executable, "-c", script], env=env, check=True, capture_output=True)  # noqa: S603

        output = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script], env=env, check=True, capture_output=True, text=True
        ).stdout

        assert 'cache_hits_total{app="Multi",cache_name="cache"} 3.0' in output
        assert "http_active_connections" in output
        assert list(tmp_path.iterdir())

    def test_main_clears_stale_files_before_serving(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        directory = tmp_path / "prom"
        directory.mkdir()
        for name in ("counter_1.db", "gauge_livesum_2.db"):
            (directory / name).write_bytes(b"stale")
        (directory / "keep.txt").write_text("x")
        monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(directory))
        monkeypatch.setenv("WEB_CONCURRENCY", "4")
        seen: list[list[str]] = []

        class FakeGranian:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.workers = kwargs["workers"]

            def serve(self) -> None:
                seen.append(sorted(p.name for p in directory.iterdir()))

        monkeypatch.setattr(app_main, "Granian", FakeGranian)

        app_main.main()

        assert seen == [["keep.txt"]]

    def test_main_warns_when_workers_share_no_directory(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
        monkeypatch.setenv("WEB_CONCURRENCY", "2")
        monkeypatch.setattr(app_main, "Granian", _NoopGranian)

        app_main.main()

        output = capsys.readouterr().out
        assert "WEB_CONCURRENCY=2" in output
        assert "PROMETHEUS_MULTIPROC_DIR is not set" in output

    def test_single_worker_does_not_warn(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
        monkeypatch.setattr(app_main, "Granian", _NoopGranian)

        app_main.main()

        assert "PROMETHEUS_MULTIPROC_DIR" not in capsys.readouterr().out


class _NoopGranian:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def serve(self) -> None:
        pass


class TestTelemetry:
    def test_disabled_by_default(self) -> None:
        settings = Settings.load()
        app = create_app(settings)

        assert setup_sentry(settings) is False
        assert setup_otel(app, settings, None) is False

    def test_logs_carry_trace_and_span_ids(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("OTEL_ENABLED", "true")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
        monkeypatch.setattr(observability, "OTLPSpanExporter", lambda **_: InMemorySpanExporter())
        monkeypatch.setenv("LOG_PRETTY_PRINT", "false")
        root = logging.getLogger()
        level, handlers = root.level, list(root.handlers)
        try:
            client = TestClient(create_app(Settings.load()))
            capsys.readouterr()
            client.get("/")
            lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x]
        finally:
            RedisInstrumentor().uninstrument()
            HTTPXClientInstrumentor().uninstrument()
            root.setLevel(level)
            root.handlers[:] = handlers

        [access] = [line for line in lines if line.get("logger") == "app.http.access"]
        assert len(access["trace_id"]) == 32
        assert len(access["span_id"]) == 16
