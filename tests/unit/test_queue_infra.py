import asyncio
import json
import logging
import re
import socket
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio import Redis

from app.common.i18n import get_i18n
from app.config import AppSettings, MailSettings, RedisSettings, Settings
from app.infrastructure import queue as queue_module
from app.infrastructure.account_cleanup import (
    AccountCleanupError,
    AccountCleanupRunner,
    register_account_cleaner,
    registered_cleaners,
)
from app.infrastructure.alerting import AlertLevel
from app.infrastructure.audit import Auditable, AuditAction, _param_id, _response_id, sanitize
from app.infrastructure.queue import RetryPolicy, TaskQueue, run_job
from app.infrastructure.redis import LockService
from app.infrastructure.scheduler import run_exclusive
from app.integrations.mail import (
    MailComposer,
    MailMessage,
    OutgoingMail,
    SmtpTransport,
    handlebars_escape,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nestjs_mail"
HASH = "eyJhbGciOi.pay-load_x"


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(queue_module, "sleep", fake_sleep)
    return delays


class TestLock:
    async def test_local_lock_is_exclusive_until_released(self) -> None:
        locks = LockService(None)
        token = await locks.try_acquire("job", 60_000)
        assert token is not None
        assert await locks.try_acquire("job", 60_000) is None
        await locks.release("job", "other-token")
        assert await locks.try_acquire("job", 60_000) is None
        await locks.release("job", token)
        assert await locks.try_acquire("job", 60_000) is not None

    async def test_local_lock_expires_after_ttl(self) -> None:
        locks = LockService(None)
        assert await locks.try_acquire("job", 5) is not None
        await asyncio.sleep(0.02)
        assert await locks.try_acquire("job", 5) is not None

    async def test_run_exclusive_skips_when_held_and_releases_after_failure(self) -> None:
        locks = LockService(None)
        held = await locks.try_acquire("scheduler:stats", 60_000)
        assert held is not None
        skipped = await run_exclusive(locks, "stats", 60_000, _answer)
        assert (skipped.executed, skipped.result) == (False, None)
        await locks.release("scheduler:stats", held)

        async def boom() -> int:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await run_exclusive(locks, "stats", 60_000, boom)
        ran = await run_exclusive(locks, "stats", 60_000, _answer)
        assert (ran.executed, ran.result) == (True, 42)


async def _answer() -> int:
    return 42


class TestRetry:
    def test_backoff_is_exponential_like_bullmq(self) -> None:
        assert [RetryPolicy(3, 1000).backoff_seconds(n) for n in (1, 2)] == [1.0, 2.0]
        assert [RetryPolicy(5, 10_000).backoff_seconds(n) for n in (1, 2, 3, 4)] == [
            10.0,
            20.0,
            40.0,
            80.0,
        ]

    async def test_retries_until_success(self, no_sleep: list[float]) -> None:
        calls = 0

        async def flaky() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError("busy")
            return "ok"

        assert await run_job("mail", RetryPolicy(3, 1000), flaky) == "ok"
        assert (calls, no_sleep) == (3, [1.0, 2.0])

    async def test_final_failure_is_reported_and_raised(self, no_sleep: list[float]) -> None:
        reported: list[BaseException] = []

        async def failing() -> None:
            raise RuntimeError("down")

        async def report(error: BaseException) -> None:
            reported.append(error)

        with pytest.raises(RuntimeError, match="down"):
            await run_job(
                "account-cleanup", RetryPolicy(5, 10_000), failing, on_final_failure=report
            )
        assert no_sleep == [10.0, 20.0, 40.0, 80.0]
        assert [str(error) for error in reported] == ["down"]

    def test_queue_is_in_process_without_redis(self) -> None:
        queue = TaskQueue.from_settings(Settings.load())
        assert queue.distributed is False

    async def test_startup_tolerates_unreachable_redis(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        monkeypatch.setenv("REDIS_ENABLED", "true")
        monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
        monkeypatch.setenv("REDIS_PORT", str(port))
        queue = TaskQueue.from_settings(Settings.load())
        assert queue.distributed is True
        with caplog.at_level(logging.ERROR, logger="app.queue"):
            await queue.startup()
        assert "Redis is unavailable" in caplog.text
        await queue.shutdown()

    def test_redis_stream_is_not_trimmed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REDIS_ENABLED", "true")
        queue = TaskQueue.from_settings(Settings.load())
        assert getattr(queue.broker, "maxlen", "missing") is None


class TestRedisSettings:
    def test_url_escapes_password(self) -> None:
        password = "p@ss/w:rd#1 %"
        settings = RedisSettings.model_validate(
            {"REDIS_HOST": "cache", "REDIS_PORT": "6380", "REDIS_PASSWORD": password, "REDIS_DB": 2}
        )
        url = settings.url()
        assert url == "redis://:p%40ss%2Fw%3Ard%231%20%25@cache:6380/2"
        kwargs = Redis.from_url(url).connection_pool.connection_kwargs
        assert (kwargs["password"], kwargs["host"], kwargs["port"], kwargs["db"]) == (
            password,
            "cache",
            6380,
            2,
        )

    def test_url_without_password(self) -> None:
        assert RedisSettings.model_validate({}).url() == "redis://localhost:6379/0"


class TestMailComposer:
    @pytest.mark.parametrize(
        ("template", "lang", "frontend", "data"),
        [
            ("activation", "en", "http://localhost:3000, https://b.example", {"hash": HASH}),
            (
                "reset-password",
                "zh",
                "https://app.example.com",
                {"hash": HASH, "tokenExpires": 1762828800000},
            ),
            ("confirm-new-email", "zh", "https://app.example.com", {"hash": HASH}),
        ],
    )
    def test_matches_nestjs_rendering(
        self, template: str, lang: str, frontend: str, data: dict[str, Any]
    ) -> None:
        app = AppSettings.model_validate(
            {"APP_NAME": "Tom's <App> & Co", "FRONTEND_DOMAIN": frontend}
        )
        mail = MailComposer(app, get_i18n("en")).compose(MailMessage(template, "a@b.c", data), lang)
        expected = json.loads((FIXTURES / f"{template}.{lang}.json").read_text(encoding="utf-8"))
        assert (mail.subject, mail.text, mail.html) == (
            expected["subject"],
            expected["text"],
            expected["html"],
        )

    def test_unknown_language_falls_back(self) -> None:
        app = AppSettings.model_validate({"FRONTEND_DOMAIN": "http://x"})
        mail = MailComposer(app, get_i18n("en")).compose(
            MailMessage("activation", "a@b.c", {"hash": "h"}), None
        )
        assert mail.subject == "Confirm email"
        assert mail.text == "http://x/confirm-email?hash=h Confirm email"

    def test_url_params_use_form_encoding(self) -> None:
        app = AppSettings.model_validate({"FRONTEND_DOMAIN": "http://x"})
        mail = MailComposer(app, get_i18n("en")).compose(
            MailMessage("activation", "a@b.c", {"hash": "a b~c+d"}), "en"
        )
        assert mail.text == "http://x/confirm-email?hash=a+b%7Ec%2Bd Confirm email"

    def test_handlebars_escape(self) -> None:
        assert handlebars_escape("a=b&c<d>'\"`") == "a&#x3D;b&amp;c&lt;d&gt;&#x27;&quot;&#x60;"
        assert handlebars_escape(None) == ""

    def test_smtp_message_headers(self) -> None:
        settings = MailSettings.model_validate(
            {"MAIL_DEFAULT_NAME": "Api", "MAIL_DEFAULT_EMAIL": "noreply@example.com"}
        )
        app = AppSettings.model_validate({"FRONTEND_DOMAIN": "http://x"})
        mail = MailComposer(app, get_i18n("en")).compose(
            MailMessage("activation", "to@example.com", {"hash": "h"}), "zh"
        )
        message = SmtpTransport(settings).build(mail)
        assert message["From"] == "Api <noreply@example.com>"
        assert message["To"] == "to@example.com"
        assert message["Subject"] == "确认电子邮件"
        assert message.get_content_type() == "multipart/alternative"
        sent_at = parsedate_to_datetime(message["Date"])
        assert abs(sent_at - datetime.now(UTC)) < timedelta(minutes=1)
        assert message["Date"].endswith("+0000")
        assert re.fullmatch(r"<[^@<>\s]+@example\.com>", message["Message-ID"])
        assert message["Message-ID"] != SmtpTransport(settings).build(mail)["Message-ID"]

    def test_smtp_subject_line_breaks_become_spaces(self) -> None:
        mail = OutgoingMail(to="to@example.com", subject="a\r\nb\nc\rd", text="t", html=None)
        message = SmtpTransport(MailSettings.model_validate({})).build(mail)
        assert message["Subject"] == "a b c d"
        assert message["Message-ID"].endswith("@localhost>")
        assert b"a b c d" in message.as_bytes()


class TestAudit:
    def test_sanitize_redacts_sensitive_keys_recursively(self) -> None:
        value = {
            "name": "x",
            "password": "p",
            "accessToken": "t",
            "hash": "h",
            "hashed": "kept",
            "nested": {"apiKey": "k", "items": [{"clientSecret": "s", "n": 1}]},
        }
        assert sanitize(value) == {
            "name": "x",
            "password": "[REDACTED]",
            "accessToken": "[REDACTED]",
            "hash": "[REDACTED]",
            "hashed": "kept",
            "nested": {"apiKey": "[REDACTED]", "items": [{"clientSecret": "[REDACTED]", "n": 1}]},
        }
        assert sanitize(None) is None
        assert sanitize({}) == {}

    def test_sanitize_truncates_deep_values(self) -> None:
        value: dict[str, Any] = {}
        node = value
        for _ in range(12):
            node["child"] = {}
            node = node["child"]
        result = sanitize(value)
        for _ in range(11):
            result = result["child"]
        assert result == {"__truncated": True}

    def test_entity_id_sources(self) -> None:
        assert _param_id({"id": "7"}, "id") == "7"
        assert _param_id({"id": "7", "date": "2026-01-01"}, ("id", "date")) == "7:2026-01-01"
        assert _param_id({"id": ""}, "id") is None
        assert _response_id({"code": 200, "message": "success", "data": {"id": 9}}) == "9"
        assert _response_id({"id": True}) is None
        assert Auditable(AuditAction.UPDATE, "User").entity_id_from == "param"


class _Cleaner:
    def __init__(self, name: str, calls: list[str], error: Exception | None = None) -> None:
        self.name = name
        self.calls = calls
        self.error = error

    async def clean(self, user_id: int) -> None:
        self.calls.append(f"{self.name}:{user_id}")
        if self.error is not None:
            raise self.error


class _Alerts:
    def __init__(self) -> None:
        self.raised: list[tuple[Any, ...]] = []

    async def raise_(self, *args: Any) -> None:
        self.raised.append(args)


class TestAccountCleanupRunner:
    async def test_runs_every_cleaner_and_aggregates_failures(self) -> None:
        calls: list[str] = []
        runner = AccountCleanupRunner(
            None,  # type: ignore[arg-type]
            [
                _Cleaner("practice", calls),
                _Cleaner("papers", calls, RuntimeError("busy")),
                _Cleaner("files", calls),
            ],
            _Alerts(),
        )
        with pytest.raises(AccountCleanupError) as caught:
            await runner.run(7)
        assert calls == ["practice:7", "papers:7", "files:7"]
        assert caught.value.failed_cleaners == ["papers"]
        assert "papers (busy)" in str(caught.value)

    async def test_report_failure_raises_deduplicated_alert(self) -> None:
        alerts = _Alerts()
        runner = AccountCleanupRunner(None, [], alerts)  # type: ignore[arg-type]
        await runner.report_failure(7, AccountCleanupError(7, ["files"], [RuntimeError("s3")]))
        [(type_, level, message, payload, dedup_key)] = alerts.raised
        assert (type_, level, dedup_key) == (
            "accountCleanupFailed",
            AlertLevel.WARN,
            "accountCleanupFailed:7",
        )
        assert "用户 7" in message
        assert payload["failedCleaners"] == ["files"]

    def test_registered_cleaners_run_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.infrastructure import account_cleanup

        monkeypatch.setattr(account_cleanup, "_CLEANERS", [])
        calls: list[str] = []

        def files(context: Any) -> _Cleaner:
            return _Cleaner("files", calls)

        def practice(context: Any) -> _Cleaner:
            return _Cleaner("practice", calls)

        register_account_cleaner(1000)(files)
        register_account_cleaner(10)(practice)
        register_account_cleaner(10)(practice)
        assert [c.name for c in registered_cleaners(None)] == ["practice", "files"]  # type: ignore[arg-type]
