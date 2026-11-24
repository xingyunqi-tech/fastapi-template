import json
import logging
from types import SimpleNamespace
from typing import Any
from uuid import UUID
lazy from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.common.client import client_ip
from app.common.errors import AppError
from app.common.masking import mask_email, mask_phone
from app.config import Quota, Settings
from app.factory import create_app
from app.infrastructure import telemetry
from app.infrastructure.events import USER_DELETED, decode, encode, user_deleted
from app.infrastructure.logging import configure_logging
from app.infrastructure.logging.middleware import level_for
from app.infrastructure.telemetry import setup_sentry, tag_request_id
from app.infrastructure.telemetry.router import monitoring_token, token_matches
from app.infrastructure.throttler import account_subject, normalize_ip
from app.infrastructure.throttler.rules import SHARED_RULES, default_rule, private_rule
from app.infrastructure.throttler.storage import MemoryGcraStore, Mode, gcra, key
from tests.tokens import access_token

PER_MINUTE = Quota(60, 60_000, 3)


class TestNormalizeIp:
    @pytest.mark.parametrize(
        ("ip", "expected"),
        [
            ("203.0.113.7", "203.0.113.7"),
            ("", ""),
            ("not-an-ip", "not-an-ip"),
            ("::1", "::1"),
            ("::ffff:198.51.100.4", "198.51.100.4"),
            ("2001:db8:abcd:12:1:2:3:4", "2001:db8:abcd:12::/64"),
            ("2001:DB8::1%eth0", "2001:db8::/64"),
        ],
    )
    def test_ipv6_uses_64_prefix(self, ip: str, expected: str) -> None:
        assert normalize_ip(ip) == expected


def _request(remote: str, forwarded: str | None) -> Any:
    headers = {} if forwarded is None else {"x-forwarded-for": forwarded}
    return SimpleNamespace(client=SimpleNamespace(host=remote), headers=headers)


class TestClientIp:
    @pytest.mark.parametrize(
        ("hops", "forwarded", "expected"),
        [
            (0, "198.51.100.1", "10.0.0.2"),
            (1, "198.51.100.1", "198.51.100.1"),
            (1, "203.0.113.9, 198.51.100.1", "198.51.100.1"),
            (2, "203.0.113.9, 198.51.100.1", "203.0.113.9"),
            (3, "203.0.113.9, 198.51.100.1", "203.0.113.9"),
            (1, None, "10.0.0.2"),
        ],
    )
    def test_hop_count_takes_nth_from_right(
        self, hops: int, forwarded: str | None, expected: str
    ) -> None:
        assert client_ip(_request("10.0.0.2", forwarded), hops) == expected

    @pytest.mark.parametrize(
        ("remote", "forwarded", "expected"),
        [
            ("10.0.0.2", "203.0.113.9, 10.1.0.1", "203.0.113.9"),
            ("10.0.0.2", "198.51.100.7, 203.0.113.9, 10.1.0.1", "203.0.113.9"),
            ("192.0.2.5", "203.0.113.9", "192.0.2.5"),
            ("10.0.0.2", "10.1.0.1", "10.1.0.1"),
            ("10.0.0.2", None, "10.0.0.2"),
        ],
    )
    def test_cidr_list_skips_trusted_hops(
        self, remote: str, forwarded: str | None, expected: str
    ) -> None:
        trusted = (
            Settings.load().app.model_validate({"TRUSTED_PROXIES": "10.0.0.0/8"}).trusted_proxies
        )

        assert client_ip(_request(remote, forwarded), trusted) == expected


class TestGcra:
    def test_burst_then_reject_with_retry_after(self) -> None:
        store = MemoryGcraStore()
        decisions = [store.consume_at("k", PER_MINUTE, Mode.HIT, now=0) for _ in range(4)]

        assert [d.allowed for d in decisions] == [True, True, True, False]
        assert [d.remaining for d in decisions[:3]] == [2, 1, 0]
        assert [d.reset_seconds for d in decisions[:3]] == [1, 2, 3]
        assert decisions[3].retry_after_seconds == 1
        assert decisions[0].limit == 3

    def test_emission_interval_replenishes_one_request(self) -> None:
        store = MemoryGcraStore()
        for _ in range(3):
            store.consume_at("k", PER_MINUTE, Mode.HIT, now=0)

        assert store.consume_at("k", PER_MINUTE, Mode.HIT, now=999).allowed is False
        later = store.consume_at("k", PER_MINUTE, Mode.HIT, now=1_000)
        assert (later.allowed, later.remaining) == (True, 0)

    def test_peek_does_not_count(self) -> None:
        store = MemoryGcraStore()
        for _ in range(5):
            assert store.consume_at("k", PER_MINUTE, Mode.PEEK, now=0).remaining == 3

        assert store.consume_at("k", PER_MINUTE, Mode.HIT, now=0).remaining == 2

    @pytest.mark.parametrize(("count", "burst"), [(3, 20), (7, 10), (10, 20)])
    def test_burst_is_exact_for_fractional_intervals(self, count: int, burst: int) -> None:
        store = MemoryGcraStore()
        quota = Quota(count, 1000, burst)
        decisions = [store.consume_at("k", quota, Mode.HIT, now=0) for _ in range(burst + 1)]

        assert [d.remaining for d in decisions[:burst]] == list(range(burst - 1, -1, -1))
        assert decisions[burst].allowed is False

    def test_stored_tat_is_integer_milliseconds(self) -> None:
        decision, stored = gcra(0, 1_000, Quota(3, 1000, 2), Mode.HIT)

        assert stored == 1_334
        assert decision.reset_ms == 334

    def test_key_layout(self) -> None:
        assert key("auth-login", "ip:203.0.113.7") == "rl:auth-login:ip:203.0.113.7"


class TestRules:
    def test_shared_rules_match_convention(self) -> None:
        table = {
            rule.name: (
                rule.quota.count,
                rule.quota.period_ms,
                rule.quota.burst,
                rule.failures_only,
            )
            for rule in SHARED_RULES
        }

        assert table == {
            "auth-login": (20, 60_000, 10, False),
            "auth-login-account": (10, 3_600_000, 5, True),
            "auth-register": (10, 3_600_000, 3, False),
            "auth-mail": (5, 3_600_000, 3, False),
            "auth-sms": (10, 3_600_000, 5, False),
            "auth-mail-account": (3, 3_600_000, 2, False),
            "auth-password-verify": (5, 900_000, 5, True),
            "auth-refresh": (30, 60_000, 10, False),
        }
        assert all(rule.shared for rule in SHARED_RULES)

    def test_private_rules_are_prefixed_with_service_name(self) -> None:
        assert default_rule("api", PER_MINUTE).name == "api-default"
        assert private_rule("api", "export", PER_MINUTE).shared is False

    def test_account_subject_is_sha256_prefix(self) -> None:
        assert account_subject("a@example.com") == "a:08168cd80dfd534a"


class TestMonitoringToken:
    @staticmethod
    def _request(token: str | None, production: bool, authorization: str | None) -> Any:
        settings = SimpleNamespace(
            telemetry=SimpleNamespace(
                metrics_token=None
                if token is None
                else SimpleNamespace(get_secret_value=lambda: token)
            ),
            app=SimpleNamespace(is_production=production),
        )
        headers = {} if authorization is None else {"authorization": authorization}
        return SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(context=SimpleNamespace(settings=settings))),
            headers=headers,
        )

    async def test_without_token_only_non_production_passes(self) -> None:
        await monitoring_token(self._request(None, False, None))

        with pytest.raises(AppError) as raised:
            await monitoring_token(self._request(None, True, None))
        assert raised.value.status == 401

    async def test_production_with_token(self) -> None:
        await monitoring_token(self._request("t" * 16, True, "Bearer " + "t" * 16))

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("Bearer secret", True),
            ("  BEARER   secret  ", True),
            ("Basic secret", False),
            (None, False),
        ],
    )
    def test_token_matching(self, header: str | None, expected: bool) -> None:
        assert token_matches(header, "secret") is expected


def _json_lines(out: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


class TestLogging:
    @pytest.fixture(autouse=True)
    def restore_root_logger(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
        monkeypatch.setenv("LOG_FORMAT", "json")
        root = logging.getLogger()
        level, handlers = root.level, list(root.handlers)
        yield
        root.setLevel(level)
        root.handlers[:] = handlers

    @pytest.mark.parametrize(
        ("status", "failed", "level"),
        [
            (200, False, logging.INFO),
            (404, False, logging.WARNING),
            (500, False, logging.ERROR),
            (200, True, logging.ERROR),
        ],
    )
    def test_level_rules(self, status: int, failed: bool, level: int) -> None:
        assert level_for(status, failed) == level

    def test_access_log_fields(self, capsys: pytest.CaptureFixture[str]) -> None:
        app = create_app(Settings.load())
        user_id = UUID("019a8f3c-6b1e-7d2a-8000-000000000007")
        token = access_token(app, user_id)
        capsys.readouterr()

        response = TestClient(app).get(
            "/api/v1/users/019a8f3c-6b1e-7d2a-8000-000000000001?token=abc",
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": "rid-1"},
        )

        [access] = [x for x in _json_lines(capsys.readouterr().out) if x["msg"] == "http request"]
        assert response.status_code == 403
        assert access["method"] == "GET"
        assert access["route"] == "/api/v1/users/{id}"
        assert access["status"] == 403
        assert access["level"] == "warn"
        assert access["request_id"] == "rid-1"
        assert access["user_id"] == str(user_id)
        assert access["service"] == "api"
        assert access["env"] == "development"
        assert access["ts"].endswith("Z")
        assert len(access["ts"]) == 24
        assert isinstance(access["duration_ms"], float)
        assert "abc" not in json.dumps(access)

    def test_unmatched_route_is_null(self, capsys: pytest.CaptureFixture[str]) -> None:
        client = TestClient(create_app(Settings.load()))
        capsys.readouterr()

        client.get("/api/v1/nope")

        [access] = [x for x in _json_lines(capsys.readouterr().out) if x["msg"] == "http request"]
        assert (access["route"], access["status"], access["level"]) == (None, 404, "warn")

    def test_secrets_are_dropped_and_contacts_masked(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(Settings.load())
        capsys.readouterr()

        logging.getLogger("app.test").warning(
            "sent %s",
            "mail",
            extra={
                "email": "alice@example.com",
                "phone": "+8613800000000",
                "password": "Secret00",
                "payload": {"refreshToken": "r", "code": "123456", "keep": 1},
            },
        )

        [line] = _json_lines(capsys.readouterr().out)
        assert line["msg"] == "sent mail"
        assert line["level"] == "warn"
        assert line["logger"] == "app.test"
        assert line["email"] == "a***@example.com"
        assert line["phone"] == "+86138****0000"
        assert "password" not in line
        assert line["payload"] == {"keep": 1}

    def test_scrubbing_ignores_case_and_enters_lists(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(Settings.load())
        capsys.readouterr()

        logging.getLogger("app.test").info(
            "request",
            extra={
                "headers": {"Authorization": "Bearer TOPSECRET", "Accept": "x"},
                "items": [{"Password": "TOPSECRET", "Email": "bob@example.com"}],
            },
        )

        [line] = _json_lines(capsys.readouterr().out)
        assert "TOPSECRET" not in json.dumps(line)
        assert line["headers"] == {"Accept": "x"}
        assert line["items"] == [{"Email": "b***@example.com"}]

    def test_http_client_request_lines_are_not_logged(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", "info")
        configure_logging(Settings.load())
        capsys.readouterr()

        logging.getLogger("httpx").info("HTTP Request: GET https://x.example?secret=TOPSECRET")
        logging.getLogger("httpx").warning("httpx warning")

        out = capsys.readouterr().out
        assert "TOPSECRET" not in out
        assert "httpx warning" in out

    def test_critical_is_reported_as_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        configure_logging(Settings.load())
        capsys.readouterr()

        logging.getLogger("app.test").critical("boom")

        [line] = _json_lines(capsys.readouterr().out)
        assert line["level"] == "error"


@pytest.mark.parametrize(
    ("value", "masked"),
    [("+8613800000000", "+86138****0000"), ("13800138000", "138****8000"), ("123", "***")],
)
def test_mask_phone(value: str, masked: str) -> None:
    assert mask_phone(value) == masked


def test_mask_email() -> None:
    assert (mask_email("alice@example.com"), mask_email("broken")) == ("a***@example.com", "***")


class TestTelemetry:
    def test_sentry_disabled_without_dsn(self) -> None:
        assert setup_sentry(Settings.load()) is False

    def test_sentry_events_carry_request_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(telemetry, "current_request_id", lambda: "rid-9")

        assert tag_request_id({}, None) == {"tags": {"request_id": "rid-9"}}

    def test_metrics_route_requires_prometheus_exporter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OTEL_METRICS_EXPORTER", "otlp")
        monkeypatch.setitem(telemetry._state, "meters", None)
        monkeypatch.setattr(telemetry, "build_metric_reader", lambda _: InMemoryMetricReader())
        assert TestClient(create_app(Settings.load())).get("/metrics").status_code == 404

    def test_http_metrics_use_stable_semantic_conventions(self) -> None:
        client = TestClient(create_app(Settings.load()))

        client.get("/health/live")
        body = client.get("/metrics").text

        [sample] = [
            line
            for line in body.splitlines()
            if line.startswith("http_server_request_duration_seconds_count{")
            and 'http_route="/health/live"' in line
        ]
        assert 'http_request_method="GET"' in sample
        assert 'http_response_status_code="200"' in sample

    def test_logs_carry_trace_and_span_ids(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
        monkeypatch.setenv("LOG_FORMAT", "json")
        monkeypatch.setattr(telemetry, "OTLPSpanExporter", InMemorySpanExporter)
        monkeypatch.setitem(telemetry._state, "tracer", None)
        root = logging.getLogger()
        level, handlers = root.level, list(root.handlers)
        try:
            client = TestClient(create_app(Settings.load()))
            capsys.readouterr()
            client.get("/health/live")
            lines = _json_lines(capsys.readouterr().out)
        finally:
            RedisInstrumentor().uninstrument()
            HTTPXClientInstrumentor().uninstrument()
            root.setLevel(level)
            root.handlers[:] = handlers

        [access] = [line for line in lines if line["msg"] == "http request"]
        assert len(access["trace_id"]) == 32
        assert len(access["span_id"]) == 16


def test_user_deleted_is_cloudevents_json() -> None:
    event = user_deleted("api", "019a8f3c-6b1e-7d2a-8000-000000000007")

    payload = json.loads(encode(event))
    assert payload["specversion"] == "1.0"
    assert payload["type"] == USER_DELETED
    assert payload["source"] == "/api"
    assert payload["data"] == {"userId": "019a8f3c-6b1e-7d2a-8000-000000000007"}
    assert decode(encode(event)).get_id() == event.get_id()
