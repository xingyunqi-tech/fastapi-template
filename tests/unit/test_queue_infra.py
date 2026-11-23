import asyncio
import contextlib
import logging
import re
import socket
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from uuid import UUID
lazy from collections.abc import AsyncIterator

import pytest

from app.common.i18n import get_i18n
from app.config import AppSettings, MailSettings, RedisSettings, Settings
from app.identity.account_deletion.cleanup import (
    AccountCleanupError,
    AccountCleanupRunner,
    register_account_cleaner,
    registered_cleaners,
)
from app.infrastructure import queue as queue_module
from app.infrastructure.audit import Audit, resource_id_of, sanitize
from app.infrastructure.mail import (
    MailComposer,
    MailMessage,
    OutgoingMail,
    SmtpTransport,
)
from app.infrastructure.queue import RetryPolicy, TaskQueue, UnrecoverableError, run_job
from app.infrastructure.redis import LockService
from app.infrastructure.scheduler import run_exclusive

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
        locks = LockService(None, "api")
        token = await locks.try_acquire("job", 60_000)
        assert token is not None
        assert await locks.try_acquire("job", 60_000) is None
        await locks.release("job", "other-token")
        assert await locks.try_acquire("job", 60_000) is None
        await locks.release("job", token)
        assert await locks.try_acquire("job", 60_000) is not None

    async def test_local_lock_expires_after_ttl(self) -> None:
        locks = LockService(None, "api")
        assert await locks.try_acquire("job", 5) is not None
        await asyncio.sleep(0.02)
        assert await locks.try_acquire("job", 5) is not None

    async def test_run_exclusive_skips_when_held_and_releases_after_failure(self) -> None:
        locks = LockService(None, "api")
        held = await locks.try_acquire("cron:stats", 60_000)
        assert held is not None
        skipped = await run_exclusive(locks, "stats", 60_000, _answer)
        assert (skipped.executed, skipped.result) == (False, None)
        await locks.release("cron:stats", held)

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

    async def test_unrecoverable_error_fails_without_retry(self, no_sleep: list[float]) -> None:
        reported: list[BaseException] = []
        calls = 0

        async def failing() -> None:
            nonlocal calls
            calls += 1
            raise UnrecoverableError("invalid openid")

        async def report(error: BaseException) -> None:
            reported.append(error)

        with pytest.raises(UnrecoverableError, match="invalid openid"):
            await run_job("mail", RetryPolicy(3, 1000), failing, on_final_failure=report)
        assert (calls, no_sleep) == (1, [])
        assert [str(error) for error in reported] == ["invalid openid"]

    def test_queue_is_in_process_without_redis(self) -> None:
        queue = TaskQueue.from_settings(Settings.load())
        assert queue.distributed is False

    async def test_startup_tolerates_unreachable_redis(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        monkeypatch.setenv("REDIS_URL", f"redis://127.0.0.1:{port}/0")
        queue = TaskQueue.from_settings(Settings.load())
        assert queue.distributed is True
        with caplog.at_level(logging.ERROR, logger="app.queue"):
            await queue.startup()
        assert "Redis is unavailable" in caplog.text
        await queue.shutdown()

    def test_redis_stream_is_prefixed_and_not_trimmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        queue = TaskQueue.from_settings(Settings.load())
        assert getattr(queue.broker, "maxlen", "missing") is None
        assert getattr(queue.broker, "queue_name", None) == "api:tasks"
        assert getattr(queue.broker, "consumer_group_name", None) == "api"


class TestRedisSettings:
    def test_url_is_taken_verbatim(self) -> None:
        url = "redis://:p%40ss@cache:6380/2"
        settings = RedisSettings.model_validate({"REDIS_URL": url})
        assert (settings.enabled, settings.url()) == (True, url)

    def test_unset_url_disables_redis(self) -> None:
        assert RedisSettings.model_validate({}).enabled is False


class TestMailComposer:
    @staticmethod
    def composer() -> MailComposer:
        app = AppSettings.model_validate({"APP_FRONTEND_URL": "http://x/"})
        return MailComposer(app, get_i18n("en"), "Demo")

    @pytest.mark.parametrize(
        ("template", "lang", "subject", "path"),
        [
            ("verify-email", "zh", "验证你的邮箱", "/verify-email"),
            ("verify-email", "en", "Verify your email", "/verify-email"),
            ("reset-password", "zh", "重置你的密码", "/reset-password"),
            ("reset-password", "en", "Reset your password", "/reset-password"),
        ],
    )
    def test_subject_body_and_link(self, template: str, lang: str, subject: str, path: str) -> None:
        mail = self.composer().compose(MailMessage(template, "a@b.c", {"token": "t"}), lang)

        assert mail.subject == subject
        assert f"http://x{path}?token=t" in mail.text
        assert "Demo" in mail.text
        assert f'href="http://x{path}?token=t"' in mail.html
        assert f'lang="{lang}"' in mail.html

    def test_unknown_language_falls_back_to_default(self) -> None:
        mail = self.composer().compose(MailMessage("verify-email", "a@b.c", {"token": "t"}), "fr")
        assert mail.subject == "Verify your email"

    def test_link_token_is_form_encoded_and_html_escaped(self) -> None:
        mail = self.composer().compose(
            MailMessage("reset-password", "a@b.c", {"token": "a b&c"}), "en"
        )
        assert "http://x/reset-password?token=a+b%26c" in mail.text
        assert 'href="http://x/reset-password?token=a+b%26c"' in mail.html

    def test_smtp_message_headers(self) -> None:
        settings = MailSettings.model_validate({"MAIL_FROM": "Api <noreply@example.com>"})
        mail = self.composer().compose(
            MailMessage("verify-email", "to@example.com", {"token": "t"}), "zh"
        )
        message = SmtpTransport(settings).build(mail)
        assert message["From"] == "Api <noreply@example.com>"
        assert message["To"] == "to@example.com"
        assert message["Subject"] == "验证你的邮箱"
        assert message.get_content_type() == "multipart/alternative"
        sent_at = parsedate_to_datetime(message["Date"])
        assert abs(sent_at - datetime.now(UTC)) < timedelta(minutes=1)
        assert message["Date"].endswith("+0000")
        assert re.fullmatch(r"<[^@<>\s]+@example\.com>", message["Message-ID"])
        assert message["Message-ID"] != SmtpTransport(settings).build(mail)["Message-ID"]

    def test_smtp_subject_line_breaks_become_spaces(self) -> None:
        mail = OutgoingMail(to="to@example.com", subject="a\r\nb\nc\rd", text="t", html="<p>t</p>")
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
            "code": "123456",
            "nested": {"apiKey": "k", "items": [{"clientSecret": "s", "n": 1}]},
        }
        assert sanitize(value) == {
            "name": "x",
            "password": "[REDACTED]",
            "accessToken": "[REDACTED]",
            "code": "[REDACTED]",
            "nested": {"apiKey": "[REDACTED]", "items": [{"clientSecret": "[REDACTED]", "n": 1}]},
        }
        assert sanitize(None) is None

    def test_sanitize_truncates_deep_values(self) -> None:
        value: dict[str, Any] = {}
        node = value
        for _ in range(12):
            node["child"] = {}
            node = node["child"]
        result = sanitize(value)
        for _ in range(10):
            result = result["child"]
        assert result == {"child": "[REDACTED]"}

    def test_resource_id_prefers_path_parameter(self) -> None:
        assert resource_id_of({"id": "7"}, "id", {"id": 9}) == "7"
        assert resource_id_of({}, "id", {"id": 9}) == "9"
        assert resource_id_of({}, "id", {"id": True}) is None
        assert resource_id_of({}, "id", None) is None
        assert Audit("user.update", resource="user").include_body is True


USER_ID = UUID("019a8f3c-6b1e-7d2a-8000-000000000007")


class _Cleaner:
    def __init__(self, name: str, calls: list[str], error: Exception | None = None) -> None:
        self.name = name
        self.calls = calls
        self.error = error

    async def clean(self, user_id: UUID) -> None:
        self.calls.append(f"{self.name}:{user_id}")
        if self.error is not None:
            raise self.error


class _RecordingDatabase:
    def __init__(self) -> None:
        self.statements: list[str] = []

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[_RecordingDatabase]:
        yield self

    async def execute(self, statement: object, params: dict[str, object]) -> None:
        self.statements.append(str(statement))


class TestAccountCleanupRunner:
    async def test_runs_every_cleaner_and_aggregates_failures(self) -> None:
        calls: list[str] = []
        database = _RecordingDatabase()
        runner = AccountCleanupRunner(
            database,  # type: ignore[arg-type]
            [
                _Cleaner("practice", calls),
                _Cleaner("papers", calls, RuntimeError("busy")),
                _Cleaner("files", calls),
            ],
        )
        with pytest.raises(AccountCleanupError) as caught:
            await runner.run(USER_ID)
        assert calls == [f"practice:{USER_ID}", f"papers:{USER_ID}", f"files:{USER_ID}"]
        assert caught.value.failed_cleaners == ["papers"]
        assert "papers (busy)" in str(caught.value)
        [statement] = database.statements
        assert "last_error = :error" in statement

    async def test_report_failure_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        runner = AccountCleanupRunner(None, [])  # type: ignore[arg-type]
        with caplog.at_level(logging.ERROR, logger="app.account_deletion"):
            await runner.report_failure(
                USER_ID, AccountCleanupError(USER_ID, ["files"], [RuntimeError("s3")])
            )
        [record] = caplog.records
        assert record.levelno == logging.ERROR
        assert record.getMessage() == f"Account cleanup for user {USER_ID} failed: files (s3)"

    def test_registered_cleaners_run_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.identity.account_deletion import cleanup as account_cleanup

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
