import asyncio
import contextlib
import email
import json
import os
import re
import signal
import socket
import sys
import threading
import time
import warnings
from email.message import EmailMessage
from email.policy import default as default_policy
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse
lazy from collections.abc import AsyncIterator, Iterator

import pytest
from redis.asyncio import Redis
from sqlalchemy import select, text
from taskiq.receiver import Receiver
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.context import AppContext
from app.common.exceptions import validation_error
from app.common.i18n import reset_language, set_language
from app.common.params import Body
from app.common.routing import ApiRouter, Auth
from app.common.security import Jwt, Passwords
from app.config import Settings, get_settings
from app.contract.schemas import AuthUpdateDto
from app.factory import create_app
from app.infrastructure import account_cleanup, metrics
from app.infrastructure import queue as queue_module
from app.infrastructure.account_cleanup import AccountCleanupProducer, pending_key
from app.infrastructure.alerting import AdminAlertService, AlertLevel
from app.infrastructure.alerting.models import AdminAlertModel
from app.infrastructure.audit import Auditable, AuditAction, AuditService
from app.infrastructure.audit.models import AuditLogModel
from app.infrastructure.cache import MemoryCache
from app.infrastructure.database.session import Database
from app.infrastructure.queue import TaskQueue, stream_name
from app.infrastructure.queue import mail as mail_queue
from app.infrastructure.queue.mail import QueueMailer
from app.infrastructure.queue.notification import record_channel_result, send_email
from app.infrastructure.redis import LockService
from app.infrastructure.scheduler import (
    cleanup_old_sessions,
    cleanup_old_sessions_task,
    cleanup_read_notifications,
    create_scheduler,
    enqueue_pending_account_cleanups,
)
from app.integrations.mail import MailMessage, SmtpTransport
from app.modules.notifications.models import NotificationModel
from tests.integration.conftest import client_for
from tests.integration.test_auth import PASSWORD, bearer, login

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from aiosmtpd.controller import Controller

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nestjs_mail"
HASH = "eyJhbGciOi.pay-load_x"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


class SmtpSink:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        self.condition = threading.Condition()

    async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:  # noqa: N802
        message = email.message_from_bytes(envelope.content, policy=default_policy)
        assert isinstance(message, EmailMessage)
        with self.condition:
            self.messages.append(message)
            self.condition.notify_all()
        return "250 OK"

    def _wait(self, count: int, seconds: float) -> bool:
        with self.condition:
            return self.condition.wait_for(lambda: len(self.messages) >= count, seconds)

    async def wait_for(self, count: int, seconds: float = 30) -> None:
        assert await asyncio.to_thread(self._wait, count, seconds), self.messages

    def html(self, index: int = 0) -> str:
        part = self.messages[index].get_body(("html",))
        assert part is not None
        return str(part.get_content()).replace("\r\n", "\n")


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> Iterator[SmtpSink]:
    sink = SmtpSink()
    port = _free_port()
    controller = Controller(sink, hostname="127.0.0.1", port=port)
    controller.start()
    for name, value in {
        "MAIL_HOST": "127.0.0.1",
        "MAIL_PORT": str(port),
        "MAIL_DEFAULT_EMAIL": "noreply@example.com",
        "MAIL_DEFAULT_NAME": "Api",
        "APP_NAME": "Tom's <App> & Co",
        "FRONTEND_DOMAIN": "https://app.example.com",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield sink
    controller.stop()


@pytest.fixture
async def redis(redis_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Redis]:
    parsed = urlparse(redis_url)
    monkeypatch.setenv("REDIS_ENABLED", "true")
    monkeypatch.setenv("REDIS_HOST", parsed.hostname or "localhost")
    monkeypatch.setenv("REDIS_PORT", str(parsed.port))
    get_settings.cache_clear()
    client = Redis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def blackhole_port() -> AsyncIterator[int]:
    async def swallow(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while await reader.read(1024):
            pass
        writer.close()

    server = await asyncio.start_server(swallow, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    server.close_clients()
    await server.wait_closed()


def use_redis_port(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    monkeypatch.setenv("REDIS_ENABLED", "true")
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_PORT", str(port))
    get_settings.cache_clear()


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(queue_module, "sleep", fake_sleep)
    return delays


@pytest.fixture
def cleaners(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    monkeypatch.setattr(account_cleanup, "_CLEANERS", [])
    return []


def make_context(database: Database, redis: Redis | None = None) -> AppContext:
    settings = Settings.load()
    queue = TaskQueue.from_settings(settings)
    context = AppContext(
        settings=settings,
        database=database,
        cache=MemoryCache(),
        passwords=Passwords(settings.auth.password_hasher),
        mailer=QueueMailer(queue, settings),
        redis=redis,
        queue=queue,
    )
    queue.bind(context)
    return context


async def consume(queue: TaskQueue, count: int) -> None:
    receiver = Receiver(queue.broker, run_startup=False, max_async_tasks=1)
    handled = 0
    async with asyncio.timeout(30), contextlib.aclosing(queue.broker.listen()) as messages:
        async for message in messages:
            await receiver.callback(message)
            handled += 1
            if handled == count:
                return


def counter(metric: Any, **labels: str) -> float:
    value: float = metric.labels(**labels)._value.get()
    return value


def fixture_mail(name: str) -> dict[str, str]:
    data: dict[str, str] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data


async def _user_id(database: Database, email_address: str) -> int:
    async with database.transaction() as session:
        value = await session.scalar(
            text('SELECT id FROM "user" WHERE email = :email'), {"email": email_address}
        )
    assert value is not None
    return int(value)


class TestLockWithRedis:
    async def test_only_one_instance_acquires(self, redis: Redis) -> None:
        first, second = LockService(redis), LockService(redis)
        tokens = await asyncio.gather(
            *(locks.try_acquire("job", 60_000) for locks in [first, second] * 5)
        )
        winners = [token for token in tokens if token is not None]
        assert len(winners) == 1
        assert await redis.get("lock:job") == winners[0].encode()
        assert 0 < await redis.pttl("lock:job") <= 60_000

        await second.release("job", "not-the-owner")
        assert await redis.exists("lock:job") == 1
        await second.release("job", winners[0])
        assert await redis.exists("lock:job") == 0

    async def test_falls_back_to_local_lock_when_redis_is_down(self) -> None:
        broken = Redis(host="127.0.0.1", port=_free_port(), socket_connect_timeout=0.2)
        locks = LockService(broken)
        token = await locks.try_acquire("job", 60_000)
        assert token is not None
        assert await locks.try_acquire("job", 60_000) is None
        await locks.release("job", token)
        assert await locks.try_acquire("job", 60_000) is not None
        await broken.aclose()


class TestMailQueue:
    @pytest.mark.parametrize(
        ("lang", "template", "data", "frontend", "fixture"),
        [
            (
                "en",
                "activation",
                {"hash": HASH},
                "http://localhost:3000, https://b.example",
                "activation.en",
            ),
            (
                "zh",
                "reset-password",
                {"hash": HASH, "tokenExpires": 1762828800000},
                "https://app.example.com",
                "reset-password.zh",
            ),
        ],
    )
    async def test_in_process_delivery_matches_nestjs(
        self,
        smtp: SmtpSink,
        connection: AsyncConnection,
        monkeypatch: pytest.MonkeyPatch,
        lang: str,
        template: str,
        data: dict[str, Any],
        frontend: str,
        fixture: str,
    ) -> None:
        monkeypatch.setenv("FRONTEND_DOMAIN", frontend)
        context = make_context(Database.bound_to(connection))
        assert context.queue is not None
        assert not context.queue.distributed
        token = set_language(lang)
        try:
            await context.mailer.send(MailMessage(template, "to@example.com", data))
        finally:
            reset_language(token)
        await context.queue.shutdown()
        await smtp.wait_for(1)

        expected = fixture_mail(fixture)
        [message] = smtp.messages
        assert message["Subject"] == expected["subject"]
        assert message["From"] == "Api <noreply@example.com>"
        assert message["To"] == "to@example.com"
        assert smtp.html().removesuffix("\n") == expected["html"]
        text_part = message.get_body(("plain",))
        assert text_part is not None
        assert str(text_part.get_content()).strip() == expected["text"]

    async def test_in_process_retries_with_backoff(
        self,
        smtp: SmtpSink,
        connection: AsyncConnection,
        monkeypatch: pytest.MonkeyPatch,
        no_sleep: list[float],
    ) -> None:
        original = SmtpTransport.send
        failures = 2

        async def flaky(self: SmtpTransport, mail: Any) -> None:
            nonlocal failures
            if failures:
                failures -= 1
                raise ConnectionError("smtp down")
            await original(self, mail)

        monkeypatch.setattr(SmtpTransport, "send", flaky)
        context = make_context(Database.bound_to(connection))
        assert context.queue is not None
        before = counter(metrics.queue_jobs_total, queue_name="mail", status="completed")
        await context.mailer.send(MailMessage("activation", "to@example.com", {"hash": "h"}))
        await context.queue.shutdown()
        await smtp.wait_for(1)
        assert no_sleep == [1.0, 2.0]
        assert (
            counter(metrics.queue_jobs_total, queue_name="mail", status="completed") == before + 1
        )

    async def test_redis_stream_delivery_and_retry(
        self,
        smtp: SmtpSink,
        redis: Redis,
        connection: AsyncConnection,
        monkeypatch: pytest.MonkeyPatch,
        no_sleep: list[float],
    ) -> None:
        original = SmtpTransport.send
        failures = 1

        async def flaky(self: SmtpTransport, mail: Any) -> None:
            nonlocal failures
            if failures:
                failures -= 1
                raise ConnectionError("smtp down")
            await original(self, mail)

        monkeypatch.setattr(SmtpTransport, "send", flaky)
        context = make_context(Database.bound_to(connection), redis)
        queue = context.queue
        assert queue is not None
        assert queue.distributed
        await queue.startup()
        try:
            await context.mailer.send(MailMessage("activation", "to@example.com", {"hash": "h"}))
            assert await redis.xlen(stream_name("mail")) == 1
            assert smtp.messages == []
            await consume(queue, 1)
        finally:
            await queue.shutdown()
        await smtp.wait_for(1)
        assert no_sleep == [1.0]
        pending = await redis.xpending(stream_name("mail"), "taskiq")
        assert pending["pending"] == 0

    async def test_worker_process_delivers_mail(
        self, smtp: SmtpSink, redis: Redis, connection: AsyncConnection
    ) -> None:
        context = make_context(Database.bound_to(connection), redis)
        assert context.queue is not None
        await context.queue.startup()
        await context.mailer.send(MailMessage("activation", "to@example.com", {"hash": "h"}))
        await context.queue.shutdown()
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from app.cli import main; main()",
            "worker",
            env=os.environ.copy(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await smtp.wait_for(1, seconds=60)
        finally:
            process.send_signal(signal.SIGTERM)
            try:
                async with asyncio.timeout(20):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
        assert smtp.messages[0]["To"] == "to@example.com"

    async def test_worker_exits_when_redis_is_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_redis_port(monkeypatch, _free_port())
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from app.cli import main; main()",
            "worker",
            env=os.environ.copy(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(30):
                _, stderr = await process.communicate()
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 1
        assert "Redis 不可用" in stderr.decode()

    async def test_falls_back_to_in_process_smtp_when_redis_refuses(
        self,
        smtp: SmtpSink,
        connection: AsyncConnection,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        use_redis_port(monkeypatch, _free_port())
        context = make_context(Database.bound_to(connection))
        assert context.queue is not None
        assert context.queue.distributed
        mailer = context.mailer
        assert isinstance(mailer, QueueMailer)
        await context.queue.startup()
        with caplog.at_level("ERROR", logger="app.queue.mail"):
            await mailer.send(MailMessage("activation", "to@example.com", {"hash": "h"}))
        assert "Failed to enqueue mail" in caplog.text
        await mailer.drain()
        await context.queue.shutdown()
        await smtp.wait_for(1)
        assert smtp.messages[0]["To"] == "to@example.com"
        text_part = smtp.messages[0].get_body(("plain",))
        assert text_part is not None
        assert "/confirm-email?hash=h" in str(text_part.get_content())

    async def test_register_does_not_hang_when_redis_blackholes(
        self,
        smtp: SmtpSink,
        connection: AsyncConnection,
        monkeypatch: pytest.MonkeyPatch,
        blackhole_port: int,
    ) -> None:
        monkeypatch.setattr(mail_queue, "ENQUEUE_TIMEOUT_S", 0.5)
        use_redis_port(monkeypatch, blackhole_port)
        app = create_app(Settings.load())
        built: AppContext = app.state.context
        assert isinstance(built.mailer, QueueMailer)
        app.state.context = AppContext(
            settings=built.settings,
            database=Database.bound_to(connection),
            cache=MemoryCache(),
            passwords=built.passwords,
            mailer=built.mailer,
            queue=built.queue,
        )
        started = time.monotonic()
        async with client_for(app) as client:
            response = await client.post(
                "/api/v1/auth/email/register",
                json={
                    "email": "blackhole@example.com",
                    "password": PASSWORD,
                    "firstName": "B",
                    "lastName": "H",
                },
            )
        elapsed = time.monotonic() - started
        assert response.json() == {"code": 200, "message": "success"}
        assert elapsed < 5
        await built.mailer.drain()
        await smtp.wait_for(1)
        assert smtp.messages[0]["To"] == "blackhole@example.com"
        assert built.redis is not None
        await built.redis.aclose()
        assert built.queue is not None
        await built.queue.broker.shutdown()


audit_router = ApiRouter(prefix="/v1/audit-probe", tags=["AuditProbe"])


@audit_router.endpoint(
    "PATCH",
    "/items/{id}",
    operation_id="auditProbeUpdate",
    auth=Auth.PUBLIC,
    audit=Auditable(AuditAction.UPDATE, "Item"),
)
async def audit_probe_update(
    id: str, body: Annotated[AuthUpdateDto, Body(AuthUpdateDto)]
) -> dict[str, Any]:
    return {"id": id, "updated": True}


@audit_router.endpoint(
    "PATCH",
    "/me/items/{id}",
    operation_id="auditProbeUpdateMine",
    auth=Auth.JWT,
    audit=Auditable(AuditAction.UPDATE, "Item"),
)
async def audit_probe_update_mine(
    id: str, body: Annotated[AuthUpdateDto, Body(AuthUpdateDto)]
) -> dict[str, Any]:
    return {"id": id, "updated": True}


@audit_router.endpoint(
    "POST",
    "/items",
    operation_id="auditProbeCreate",
    auth=Auth.PUBLIC,
    audit=Auditable(AuditAction.CREATE, "Item", include_response=True),
)
async def audit_probe_create() -> dict[str, Any]:
    return {"id": 99, "name": "n", "accessToken": "t"}


@audit_router.endpoint(
    "POST",
    "/fail",
    operation_id="auditProbeFail",
    auth=Auth.PUBLIC,
    audit=Auditable(AuditAction.DELETE, "Item"),
)
async def audit_probe_fail() -> None:
    raise validation_error("item", "notFound")


@pytest.fixture
def audit_app(connection: AsyncConnection) -> FastAPI:
    get_settings.cache_clear()
    settings = Settings.load()
    app = create_app(settings, extra_routers=[(audit_router, "/api")])
    app.state.context = AppContext(
        settings=settings,
        database=Database.bound_to(connection),
        cache=MemoryCache(),
        passwords=Passwords(settings.auth.password_hasher),
        mailer=app.state.context.mailer,
    )
    return app


async def _scalar(connection: AsyncConnection, sql: str, **params: Any) -> Any:
    async with Database.bound_to(connection).transaction() as session:
        return await session.scalar(text(sql), params)


async def _audit_logs(connection: AsyncConnection) -> list[AuditLogModel]:
    async with Database.bound_to(connection).transaction() as session:
        return list(await session.scalars(select(AuditLogModel).order_by(AuditLogModel.id)))


class TestAudit:
    async def test_records_successful_request(
        self, audit_app: FastAPI, connection: AsyncConnection
    ) -> None:
        secret = audit_app.state.settings.auth.secret.get_secret_value()
        token = Jwt.sign({"id": 7, "role": {"id": 1}, "sessionId": 1}, secret, "15m")
        async with client_for(audit_app) as client:
            response = await client.patch(
                "/api/v1/audit-probe/items/5",
                json={"firstName": "Ann", "oldPassword": PASSWORD, "unknown": 1},
                headers={
                    **bearer(token),
                    "X-Forwarded-For": "198.51.100.1, 10.0.0.1",
                    "User-Agent": "probe/1.0",
                },
            )
        assert response.status_code == 200
        [log] = await _audit_logs(connection)
        assert (log.action, log.entity_type, log.entity_id, log.user_id) == (
            "UPDATE",
            "Item",
            "5",
            None,
        )
        assert log.old_value is None
        assert log.new_value == {"firstName": "Ann", "oldPassword": "[REDACTED]"}
        assert (log.ip_address, log.user_agent) == ("198.51.100.1", "probe/1.0")
        assert log.request_id == response.headers["x-request-id"]
        assert await _scalar(
            connection,
            'SELECT "oldValue" IS NULL AND "userId" IS NULL FROM audit_logs WHERE id = :id',
            id=log.id,
        )

    async def test_records_user_of_authenticated_route(
        self, audit_app: FastAPI, connection: AsyncConnection
    ) -> None:
        async with client_for(audit_app) as client:
            tokens = await login(client, "john.doe@example.com")
            response = await client.patch(
                "/api/v1/audit-probe/me/items/5",
                json={"firstName": "Ann"},
                headers=bearer(tokens["token"]),
            )
        assert response.status_code == 200
        [log] = await _audit_logs(connection)
        assert (log.entity_id, log.user_id) == ("5", str(tokens["user"]["id"]))

    async def test_null_values_are_stored_as_sql_null(self, connection: AsyncConnection) -> None:
        log = await AuditService(Database.bound_to(connection)).log(
            action=AuditAction.DELETE, entity_type="Item", entity_id=1
        )
        assert await _scalar(
            connection,
            'SELECT "oldValue" IS NULL AND "newValue" IS NULL FROM audit_logs WHERE id = :id',
            id=log.id,
        )

    async def test_records_response_and_skips_failures(
        self, audit_app: FastAPI, connection: AsyncConnection
    ) -> None:
        async with client_for(audit_app) as client:
            created = await client.post("/api/v1/audit-probe/items")
            failed = await client.post("/api/v1/audit-probe/fail")
        assert (created.status_code, failed.status_code) == (200, 422)
        [log] = await _audit_logs(connection)
        assert (log.action, log.entity_id, log.user_id, log.ip_address) == (
            "CREATE",
            "99",
            None,
            "203.0.113.7",
        )
        assert log.new_value == {"id": 99, "name": "n", "accessToken": "[REDACTED]"}

    async def test_audit_failure_does_not_affect_response(
        self, audit_app: FastAPI, connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def broken(self: AuditService, **kwargs: Any) -> None:
            raise RuntimeError("db down")

        monkeypatch.setattr(AuditService, "log", broken)
        async with client_for(audit_app) as client:
            response = await client.post("/api/v1/audit-probe/items")
        assert response.status_code == 200
        assert await _audit_logs(connection) == []


class TestAlerting:
    async def test_deduplicates_open_alerts_and_notifies_admins(
        self, connection: AsyncConnection
    ) -> None:
        database = Database.bound_to(connection)
        alerts = AdminAlertService(database)
        raised_before = counter(metrics.admin_alerts_total, type="probe", level="WARN")
        dedup_before = counter(metrics.admin_alerts_deduplicated_total, type="probe")

        first = await alerts.raise_("probe", AlertLevel.WARN, "disk full", {"disk": "/"}, "probe:1")
        second = await alerts.raise_("probe", AlertLevel.WARN, "disk full again", None, "probe:1")
        other = await alerts.raise_("probe", AlertLevel.WARN, "other", None, "probe:2")
        assert first is not None
        assert other is not None
        assert second is None
        assert counter(metrics.admin_alerts_total, type="probe", level="WARN") == raised_before + 2
        assert counter(metrics.admin_alerts_deduplicated_total, type="probe") == dedup_before + 1

        async with database.transaction() as session:
            rows = list(await session.scalars(select(AdminAlertModel).order_by(AdminAlertModel.id)))
            assert [(r.dedup_key, r.message, r.payload) for r in rows] == [
                ("probe:1", "disk full", {"disk": "/"}),
                ("probe:2", "other", None),
            ]
            notifications = list(
                await session.scalars(
                    select(NotificationModel).where(NotificationModel.title == "[WARN] probe")
                )
            )
            admin_count = await session.scalar(
                text('SELECT count(*) FROM "user" WHERE "roleId" = 1 AND "deletedAt" IS NULL')
            )
            await session.execute(
                text('UPDATE admin_alert SET "acknowledgedAt" = now() WHERE "dedupKey" = :key'),
                {"key": "probe:1"},
            )
        assert len(notifications) == 2 * int(admin_count or 0) > 0
        sample = notifications[0]
        assert (sample.type, sample.category, sample.channels, sample.content) == (
            "WARNING",
            "SYSTEM",
            ["IN_APP"],
            "disk full",
        )
        assert sample.metadata_ == {"alertId": first.id, "dedupKey": "probe:1"}

        again = await alerts.raise_("probe", AlertLevel.CRITICAL, "disk full", None, "probe:1")
        assert again is not None
        assert again.id != first.id
        assert (
            await _scalar(
                connection,
                'SELECT count(*) FROM admin_alert WHERE "dedupKey" = :key AND payload IS NULL',
                key="probe:2",
            )
            == 1
        )


class TestAccountCleanup:
    async def test_delete_account_runs_cleaners_in_process(
        self, committed_app: FastAPI, cleaners: list[tuple[str, int]]
    ) -> None:
        @account_cleanup.register_account_cleaner(10)
        class Probe:
            name = "probe"

            def __init__(self, context: AppContext) -> None:
                self.context = context

            async def clean(self, user_id: int) -> None:
                cleaners.append(("probe", user_id))

        context: AppContext = committed_app.state.context
        context.queue = TaskQueue.from_settings(context.settings)
        context.queue.bind(context)
        async with client_for(committed_app) as client:
            tokens = await login(client, "john.doe@example.com")
            deleted = await client.request(
                "DELETE",
                "/api/v1/auth/me",
                json={"oldPassword": PASSWORD},
                headers=bearer(tokens["token"]),
            )
        assert deleted.status_code == 200
        await context.queue.shutdown()
        user_id = tokens["user"]["id"]
        assert cleaners == [("probe", user_id)]
        async with context.database.transaction() as session:
            completed = await session.scalar(
                text('SELECT "completedAt" FROM account_cleanup WHERE "userId" = :id'),
                {"id": user_id},
            )
            active_sessions = await session.scalar(
                text('SELECT count(*) FROM session WHERE "userId" = :id AND "deletedAt" IS NULL'),
                {"id": user_id},
            )
        assert completed is not None
        assert active_sessions == 0

    async def test_final_failure_raises_alert(
        self,
        committed_app: FastAPI,
        cleaners: list[tuple[str, int]],
        no_sleep: list[float],
    ) -> None:
        @account_cleanup.register_account_cleaner(10)
        class Failing:
            name = "files"

            def __init__(self, context: AppContext) -> None:
                pass

            async def clean(self, user_id: int) -> None:
                raise RuntimeError("s3 down")

        context: AppContext = committed_app.state.context
        context.queue = TaskQueue.from_settings(context.settings)
        context.queue.bind(context)
        user_id = await _user_id(context.database, "john.doe@example.com")
        await AccountCleanupProducer(context).enqueue_cleanup(user_id)
        await context.queue.shutdown()
        assert no_sleep == [10.0, 20.0, 40.0, 80.0]
        async with context.database.transaction() as session:
            alert = await session.scalar(
                select(AdminAlertModel).where(
                    AdminAlertModel.dedup_key == f"accountCleanupFailed:{user_id}"
                )
            )
            completed = await session.scalar(
                text('SELECT count(*) FROM account_cleanup WHERE "userId" = :id'), {"id": user_id}
            )
        assert alert is not None
        assert (alert.type, alert.level) == ("accountCleanupFailed", "WARN")
        assert alert.payload == {
            "userId": user_id,
            "reason": f"Account cleanup for user {user_id} failed: files (s3 down)",
            "failedCleaners": ["files"],
        }
        assert completed == 0

    async def test_redis_queue_deduplicates_pending_jobs(
        self, committed_app: FastAPI, redis: Redis, cleaners: list[tuple[str, int]]
    ) -> None:
        held: list[int] = []

        @account_cleanup.register_account_cleaner(10)
        class Probe:
            name = "probe"

            def __init__(self, context: AppContext) -> None:
                pass

            async def clean(self, user_id: int) -> None:
                cleaners.append(("probe", user_id))
                held.append(await redis.exists(f"lock:account-cleanup:user-{user_id}"))

        app_context: AppContext = committed_app.state.context
        context = make_context(app_context.database, redis)
        queue = context.queue
        assert queue is not None
        await queue.startup()
        user_id = await _user_id(context.database, "john.doe@example.com")
        producer = AccountCleanupProducer(context)
        try:
            await producer.enqueue_cleanup(user_id)
            await producer.enqueue_cleanup(user_id)
            assert await redis.xlen(stream_name("account-cleanup")) == 1
            assert await redis.exists(pending_key(user_id)) == 1
            assert await redis.pttl(pending_key(user_id)) > 23 * 60 * 60 * 1000
            await consume(queue, 1)
        finally:
            await queue.shutdown()
        assert cleaners == [("probe", user_id)]
        assert held == [1]
        assert await redis.exists(pending_key(user_id)) == 0
        assert await redis.exists(f"lock:account-cleanup:user-{user_id}") == 0

    async def test_skips_user_locked_by_another_worker(
        self, committed_app: FastAPI, redis: Redis, cleaners: list[tuple[str, int]]
    ) -> None:
        @account_cleanup.register_account_cleaner(10)
        class Probe:
            name = "probe"

            def __init__(self, context: AppContext) -> None:
                pass

            async def clean(self, user_id: int) -> None:
                cleaners.append(("probe", user_id))

        app_context: AppContext = committed_app.state.context
        context = make_context(app_context.database, redis)
        queue = context.queue
        assert queue is not None
        await queue.startup()
        user_id = await _user_id(context.database, "john.doe@example.com")
        other_worker = LockService(redis)
        token = await other_worker.try_acquire(f"account-cleanup:user-{user_id}", 60_000)
        assert token is not None
        try:
            await AccountCleanupProducer(context).enqueue_cleanup(user_id)
            await consume(queue, 1)
        finally:
            await queue.shutdown()
        assert cleaners == []
        assert await redis.exists(pending_key(user_id)) == 1
        assert await redis.get(f"lock:account-cleanup:user-{user_id}") == token.encode()
        await other_worker.release(f"account-cleanup:user-{user_id}", token)
        await redis.delete(pending_key(user_id))


async def _insert_notification(
    connection: AsyncConnection, user_id: int, *, deleted: bool = False
) -> int:
    async with Database.bound_to(connection).transaction() as session:
        notification_id = await session.scalar(
            text(
                'INSERT INTO notification ("userId", type, category, title, content, channels, '
                '"updatedAt", "deletedAt") VALUES '
                "(:u, 'INFO', 'SYSTEM', 'Hi', 'Body', '[\"EMAIL\"]', NOW() - INTERVAL '1 day', "
                "CASE WHEN :deleted THEN NOW() - INTERVAL '1 hour' END) RETURNING id"
            ),
            {"u": user_id, "deleted": deleted},
        )
    return int(notification_id)


async def _notification_row(connection: AsyncConnection, notification_id: int) -> Any:
    async with Database.bound_to(connection).transaction() as session:
        result = await session.execute(
            text(
                'SELECT "sentChannels", "updatedAt" > NOW() - INTERVAL \'1 hour\' AS touched '
                "FROM notification WHERE id = :id"
            ),
            {"id": notification_id},
        )
        return result.one()


class TestNotificationQueue:
    async def test_send_email_records_success_and_touches_updated_at(
        self, smtp: SmtpSink, connection: AsyncConnection
    ) -> None:
        context = make_context(Database.bound_to(connection))
        assert context.queue is not None
        user_id = await _user_id(context.database, "john.doe@example.com")
        notification_id = await _insert_notification(connection, user_id)
        await context.queue.enqueue(
            send_email,
            {
                "notificationId": notification_id,
                "userId": user_id,
                "title": "Hi",
                "content": "Body",
            },
        )
        await context.queue.shutdown()
        await smtp.wait_for(1)
        [message] = smtp.messages
        assert (message["To"], message["Subject"]) == ("john.doe@example.com", "Hi")
        row = await _notification_row(connection, notification_id)
        assert row.touched
        assert set(row.sentChannels) == {"EMAIL"}
        assert row.sentChannels["EMAIL"]["sent"] is True
        assert re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", row.sentChannels["EMAIL"]["sentAt"]
        )

    async def test_send_email_records_failure_when_user_is_missing(
        self, smtp: SmtpSink, connection: AsyncConnection, no_sleep: list[float]
    ) -> None:
        context = make_context(Database.bound_to(connection))
        assert context.queue is not None
        user_id = await _user_id(context.database, "john.doe@example.com")
        notification_id = await _insert_notification(connection, user_id)
        before = counter(metrics.queue_jobs_total, queue_name="notification", status="failed")
        await context.queue.enqueue(
            send_email,
            {"notificationId": notification_id, "userId": 987654, "title": "Hi", "content": "B"},
        )
        await context.queue.shutdown()
        assert no_sleep == [1.0, 2.0]
        assert smtp.messages == []
        assert (
            counter(metrics.queue_jobs_total, queue_name="notification", status="failed")
            == before + 1
        )
        row = await _notification_row(connection, notification_id)
        assert row.touched
        result = row.sentChannels["EMAIL"]
        assert (result["sent"], result["error"]) == (
            False,
            "User 987654 not found or has no email",
        )

    async def test_record_channel_result_skips_soft_deleted_notification(
        self, connection: AsyncConnection
    ) -> None:
        context = make_context(Database.bound_to(connection))
        user_id = await _user_id(context.database, "john.doe@example.com")
        notification_id = await _insert_notification(connection, user_id, deleted=True)
        await record_channel_result(context, notification_id, "EMAIL", True)
        row = await _notification_row(connection, notification_id)
        assert row.sentChannels == {}
        assert not row.touched


class TestLifespan:
    @pytest.mark.parametrize("slow_work", ["throttler", "mail"])
    async def test_shutdown_waits_for_pending_background_work(
        self, monkeypatch: pytest.MonkeyPatch, slow_work: str
    ) -> None:
        monkeypatch.setenv("MAIL_HOST", "127.0.0.1")
        get_settings.cache_clear()
        app = create_app(Settings.load())
        context: AppContext = app.state.context
        mailer = context.mailer
        assert isinstance(mailer, QueueMailer)
        finished: list[str] = []
        finished_before_dispose: list[str] = []
        dispose = context.database.dispose

        async def recording_dispose() -> None:
            finished_before_dispose.extend(finished)
            await dispose()

        monkeypatch.setattr(context.database, "dispose", recording_dispose)

        async def slow(name: str) -> None:
            await asyncio.sleep(0.3 if name == slow_work else 0)
            finished.append(name)

        async with app.router.lifespan_context(app):
            throttler_task = asyncio.create_task(slow("throttler"))
            app.state.throttler._pending.add(throttler_task)
            mail_task = asyncio.create_task(slow("mail"))
            mailer.pending.add(mail_task)
        assert sorted(finished_before_dispose) == ["mail", "throttler"]


class TestScheduler:
    async def test_cleanup_conditions(self, committed_app: FastAPI) -> None:
        context: AppContext = committed_app.state.context
        user_id = await _user_id(context.database, "john.doe@example.com")
        async with context.database.transaction() as session:
            await session.execute(
                text(
                    'INSERT INTO session (hash, "userId", "deletedAt") VALUES '
                    "('old', :u, NOW() - INTERVAL '8 days'), "
                    "('recent', :u, NOW() - INTERVAL '6 days'), ('active', :u, NULL)"
                ),
                {"u": user_id},
            )
            await session.execute(
                text(
                    'INSERT INTO notification ("userId", type, category, title, content, '
                    '"isRead", "readAt") VALUES '
                    "(:u, 'INFO', 'SYSTEM', 'old', 'c', true, NOW() - INTERVAL '31 days'), "
                    "(:u, 'INFO', 'SYSTEM', 'recent', 'c', true, NOW() - INTERVAL '29 days'), "
                    "(:u, 'INFO', 'SYSTEM', 'unread', 'c', false, NULL)"
                ),
                {"u": user_id},
            )

        context.queue = TaskQueue.from_settings(context.settings)
        context.queue.bind(context)
        token = await context.locks.try_acquire("scheduler:cleanup-old-sessions", 60_000)
        assert token is not None
        await context.queue.enqueue(cleanup_old_sessions_task)
        await context.queue.shutdown()
        async with context.database.transaction() as session:
            assert (
                await session.scalar(text("SELECT count(*) FROM session WHERE hash = 'old'")) == 1
            )
        await context.locks.release("scheduler:cleanup-old-sessions", token)

        await context.queue.enqueue(cleanup_old_sessions_task)
        await context.queue.shutdown()
        assert await cleanup_old_sessions(context) == 0
        assert await cleanup_read_notifications(context) == 1
        async with context.database.transaction() as session:
            hashes = await session.scalars(
                text("SELECT hash FROM session WHERE hash IN ('old', 'recent', 'active')")
            )
            assert sorted(hashes) == ["active", "recent"]
            deleted = await session.scalars(
                text('SELECT title FROM notification WHERE "deletedAt" IS NOT NULL')
            )
            assert list(deleted) == ["old"]

    async def test_compensation_enqueues_users_deleted_before_grace(
        self, committed_app: FastAPI, cleaners: list[tuple[str, int]]
    ) -> None:
        @account_cleanup.register_account_cleaner(10)
        class Probe:
            name = "probe"

            def __init__(self, context: AppContext) -> None:
                pass

            async def clean(self, user_id: int) -> None:
                cleaners.append(("probe", user_id))

        context: AppContext = committed_app.state.context
        context.queue = TaskQueue.from_settings(context.settings)
        context.queue.bind(context)
        old_id = await _user_id(context.database, "john.doe@example.com")
        new_id = await _user_id(context.database, "admin@example.com")
        async with context.database.transaction() as session:
            await session.execute(
                text(
                    'UPDATE "user" SET "deletedAt" = NOW() - INTERVAL \'31 minutes\' WHERE id = :id'
                ),
                {"id": old_id},
            )
            await session.execute(
                text(
                    'UPDATE "user" SET "deletedAt" = NOW() - INTERVAL \'10 minutes\' WHERE id = :id'
                ),
                {"id": new_id},
            )
        assert await enqueue_pending_account_cleanups(context) == 1
        await context.queue.shutdown()
        assert cleaners == [("probe", old_id)]
        assert await enqueue_pending_account_cleanups(context) == 0

    async def test_schedules(self) -> None:
        queue = TaskQueue.from_settings(Settings.load())
        scheduler = create_scheduler(queue)
        [source] = scheduler.sources
        await source.startup()
        schedules = {
            item.task_name: (item.cron, item.cron_offset) for item in await source.get_schedules()
        }
        assert schedules == {
            "scheduler:cleanup-old-sessions": ("0 3 * * *", "UTC"),
            "scheduler:cleanup-read-notifications": ("0 4 * * *", "UTC"),
            "scheduler:account-cleanup-compensation": ("25 * * * *", "Asia/Shanghai"),
        }
