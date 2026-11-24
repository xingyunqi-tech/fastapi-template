import asyncio
import contextlib
import email
import json
import os
import signal
import socket
import sys
import time
from email.message import EmailMessage
from email.policy import default as default_policy
from typing import Any
from uuid import UUID, uuid7
lazy from collections.abc import AsyncIterator

import httpx as httpx_lib
import pytest
from redis.asyncio import Redis
from sqlalchemy import select, text
from taskiq.receiver import Receiver
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.context import AppContext
from app.common.errors import field_error
from app.common.pagination import encode_cursor
from app.common.routing import ApiRouter, Auth
from app.config import Settings, get_settings
from app.factory import build_tokens, create_app
from app.identity.account_deletion.cleanup import (
    AccountCleanupProducer,
    AccountCleanupRunner,
    pending_key,
)
from app.identity.sessions.account_cleaner import SessionsAccountCleaner
from app.infrastructure import account_data as account_cleanup
from app.infrastructure import audit as audit_module
from app.infrastructure import queue as queue_module
from app.infrastructure.audit import Audit, write_audit_log
from app.infrastructure.audit.models import AuditLogModel
from app.infrastructure.cache import MemoryCache
from app.infrastructure.database.session import Database
from app.infrastructure.events import USER_DELETED, decode
from app.infrastructure.mail import MailMessage, SmtpTransport
from app.infrastructure.queue import TaskQueue, stream_name
from app.infrastructure.queue import mail as mail_queue
from app.infrastructure.queue.mail import QueueMailer
from app.infrastructure.redis import LockService
from app.infrastructure.scheduler import (
    cleanup_old_sessions,
    cleanup_old_sessions_task,
    create_scheduler,
    enqueue_pending_account_cleanups,
)
from app.security.passwords import Passwords
from tests.integration.conftest import Mailpit, _build_app, client_for
from tests.integration.identity import ADMIN_EMAIL, PASSWORD, USER_EMAIL, bearer, login
from tests.tokens import access_token
lazy from app.identity.me.schemas import UpdateMeRequest

USER_ID = UUID("019a8f3c-6b1e-7d2a-8000-000000000007")
SERVICE = "api"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


class MailpitSink:
    def __init__(self, server: Mailpit) -> None:
        self.server = server
        self.messages: list[EmailMessage] = []

    def clear(self) -> None:
        httpx_lib.delete(f"{self.server.api}/api/v1/messages", trust_env=False).raise_for_status()

    async def wait_for(self, count: int, seconds: float = 30) -> None:
        async with httpx_lib.AsyncClient(base_url=self.server.api, trust_env=False) as http:
            async with asyncio.timeout(seconds):
                while True:
                    listed = (await http.get("/api/v1/messages")).json()["messages"]
                    if len(listed) >= count:
                        break
                    await asyncio.sleep(0.1)
            self.messages = []
            for item in reversed(listed):
                raw = (await http.get(f"/api/v1/message/{item['ID']}/raw")).content
                message = email.message_from_bytes(raw, policy=default_policy)
                assert isinstance(message, EmailMessage)
                self.messages.append(message)


@pytest.fixture
def smtp(mailpit_server: Mailpit, monkeypatch: pytest.MonkeyPatch) -> MailpitSink:
    sink = MailpitSink(mailpit_server)
    sink.clear()
    for name, value in {
        "MAIL_HOST": mailpit_server.host,
        "MAIL_PORT": str(mailpit_server.smtp_port),
        "MAIL_FROM": "Api <noreply@example.com>",
        "MAIL_TLS": "none",
        "APP_FRONTEND_URL": "https://app.example.com",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    return sink


@pytest.fixture
async def redis(redis_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Redis]:
    monkeypatch.setenv("REDIS_URL", redis_url)
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
    monkeypatch.setenv("REDIS_URL", f"redis://127.0.0.1:{port}/0")
    get_settings.cache_clear()


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(queue_module, "sleep", fake_sleep)
    return delays


@pytest.fixture
def cleaners(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, UUID]]:
    monkeypatch.setattr(account_cleanup, "_CLEANERS", [])
    return []


def make_context(database: Database, redis: Redis | None = None) -> AppContext:
    settings = Settings.load()
    signer, verifier = build_tokens(settings)
    queue = TaskQueue.from_settings(settings)
    context = AppContext(
        settings=settings,
        database=database,
        cache=MemoryCache(),
        passwords=Passwords(),
        mailer=QueueMailer(queue, settings),
        signer=signer,
        verifier=verifier,
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


async def _user_id(database: Database, email_address: str) -> UUID:
    async with database.transaction() as session:
        value = await session.scalar(
            text("SELECT id FROM users WHERE email = :email"), {"email": email_address}
        )
    assert isinstance(value, UUID)
    return value


async def _request_deletion(database: Database, user_id: UUID, minutes_ago: int = 0) -> None:
    async with database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO account_deletions (user_id, requested_at) "
                "VALUES (:id, now() - make_interval(mins => :minutes))"
            ),
            {"id": user_id, "minutes": minutes_ago},
        )


class TestLockWithRedis:
    async def test_only_one_instance_acquires(self, redis: Redis) -> None:
        first, second = LockService(redis, SERVICE), LockService(redis, SERVICE)
        tokens = await asyncio.gather(
            *(locks.try_acquire("job", 60_000) for locks in [first, second] * 5)
        )
        winners = [token for token in tokens if token is not None]
        assert len(winners) == 1
        assert await redis.get("api:lock:job") == winners[0].encode()
        assert 0 < await redis.pttl("api:lock:job") <= 60_000

        await second.release("job", "not-the-owner")
        assert await redis.exists("api:lock:job") == 1
        await second.release("job", winners[0])
        assert await redis.exists("api:lock:job") == 0

    async def test_falls_back_to_local_lock_when_redis_is_down(self) -> None:
        broken = Redis(host="127.0.0.1", port=_free_port(), socket_connect_timeout=0.2)
        locks = LockService(broken, SERVICE)
        token = await locks.try_acquire("job", 60_000)
        assert token is not None
        assert await locks.try_acquire("job", 60_000) is None
        await locks.release("job", token)
        assert await locks.try_acquire("job", 60_000) is not None
        await broken.aclose()


class TestMailQueue:
    async def test_in_process_retries_with_backoff(
        self,
        smtp: MailpitSink,
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
        await context.mailer.send(MailMessage("verify-email", "to@example.com", {"token": "t"}))
        await context.queue.shutdown()
        await smtp.wait_for(1)
        assert no_sleep == [1.0, 2.0]

    async def test_redis_stream_delivery_and_retry(
        self,
        smtp: MailpitSink,
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
            await context.mailer.send(MailMessage("verify-email", "to@example.com", {"token": "t"}))
            assert await redis.xlen(stream_name(SERVICE)) == 1
            assert smtp.messages == []
            await consume(queue, 1)
        finally:
            await queue.shutdown()
        await smtp.wait_for(1)
        assert no_sleep == [1.0]
        pending = await redis.xpending(stream_name(SERVICE), SERVICE)
        assert pending["pending"] == 0

    async def test_worker_process_delivers_mail(
        self, smtp: MailpitSink, redis: Redis, connection: AsyncConnection
    ) -> None:
        context = make_context(Database.bound_to(connection), redis)
        assert context.queue is not None
        await context.queue.startup()
        await context.mailer.send(MailMessage("verify-email", "to@example.com", {"token": "t"}))
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
        smtp: MailpitSink,
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
            await mailer.send(MailMessage("verify-email", "to@example.com", {"token": "t"}))
        assert "Failed to enqueue mail" in caplog.text
        await mailer.drain()
        await context.queue.shutdown()
        await smtp.wait_for(1)
        assert smtp.messages[0]["To"] == "to@example.com"
        text_part = smtp.messages[0].get_body(("plain",))
        assert text_part is not None
        assert "https://app.example.com/verify-email?token=t" in str(text_part.get_content())

    async def test_register_does_not_hang_when_redis_blackholes(
        self,
        smtp: MailpitSink,
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
            signer=built.signer,
            verifier=built.verifier,
            queue=built.queue,
        )
        started = time.monotonic()
        async with client_for(app) as client:
            response = await client.post(
                "/api/v1/auth/email/register",
                json={"email": "blackhole@example.com", "password": PASSWORD},
            )
        elapsed = time.monotonic() - started
        assert response.status_code == 201
        assert elapsed < 5
        await built.mailer.drain()
        await smtp.wait_for(1)
        assert smtp.messages[0]["To"] == "blackhole@example.com"
        assert built.redis is not None
        await built.redis.aclose()
        assert built.queue is not None
        await built.queue.broker.shutdown()

    async def test_mail_language_follows_request(
        self, smtp: MailpitSink, connection: AsyncConnection
    ) -> None:
        app = create_app(Settings.load())
        built: AppContext = app.state.context
        assert isinstance(built.mailer, QueueMailer)
        app.state.context = AppContext(
            settings=built.settings,
            database=Database.bound_to(connection),
            cache=MemoryCache(),
            passwords=built.passwords,
            mailer=built.mailer,
            signer=built.signer,
            verifier=built.verifier,
            queue=built.queue,
        )
        async with client_for(app) as client:
            for address, language in (("zh@example.com", "zh-CN"), ("en@example.com", "en")):
                response = await client.post(
                    "/api/v1/auth/email/register",
                    json={"email": address, "password": PASSWORD},
                    headers={"Accept-Language": language},
                )
                assert response.status_code == 201, response.text
        await built.mailer.drain()
        await smtp.wait_for(2)
        subjects = {message["To"]: message["Subject"] for message in smtp.messages}
        assert subjects == {"zh@example.com": "验证你的邮箱", "en@example.com": "Verify your email"}
        html = smtp.messages[0].get_body(("html",))
        assert html is not None
        assert "https://app.example.com/verify-email?token=" in str(html.get_content())
        assert built.queue is not None
        await built.queue.shutdown()


audit_router = ApiRouter(prefix="/v1/audit-probe", tags=["AuditProbe"])


@audit_router.endpoint(
    "PATCH",
    "/items/{id}",
    operation_id="updateAuditProbe",
    auth=Auth.PUBLIC,
    audit=Audit("item.update", resource="item"),
)
async def audit_probe_update(id: str, body: UpdateMeRequest) -> dict[str, Any]:
    return {"id": id, "updated": True}


@audit_router.endpoint(
    "PATCH",
    "/me/items/{id}",
    operation_id="updateAuditProbeMine",
    auth=Auth.JWT,
    audit=Audit("item.update", resource="item"),
)
async def audit_probe_update_mine(id: str, body: UpdateMeRequest) -> dict[str, Any]:
    return {"id": id, "updated": True}


@audit_router.endpoint(
    "POST",
    "/items",
    operation_id="createAuditProbe",
    auth=Auth.PUBLIC,
    audit=Audit("item.create", resource="item"),
)
async def audit_probe_create() -> dict[str, Any]:
    return {"id": 99, "name": "n"}


@audit_router.endpoint(
    "POST",
    "/fail",
    operation_id="createAuditProbeFailure",
    auth=Auth.PUBLIC,
    audit=Audit("item.delete", resource="item"),
)
async def audit_probe_fail() -> None:
    raise field_error("item", "notFound")


@pytest.fixture
def audit_app(connection: AsyncConnection) -> FastAPI:
    get_settings.cache_clear()
    settings = Settings.load()
    app = create_app(settings, extra_routers=[(audit_router, "/api")])
    app.state.context = AppContext(
        settings=settings,
        database=Database.bound_to(connection),
        cache=MemoryCache(),
        passwords=Passwords(),
        mailer=app.state.context.mailer,
        signer=app.state.context.signer,
        verifier=app.state.context.verifier,
    )
    return app


async def _scalar(connection: AsyncConnection, sql: str, **params: Any) -> Any:
    async with Database.bound_to(connection).transaction() as session:
        return await session.scalar(text(sql), params)


async def _audit_logs(connection: AsyncConnection) -> list[AuditLogModel]:
    async with Database.bound_to(connection).transaction() as session:
        return list(await session.scalars(select(AuditLogModel).order_by(AuditLogModel.created_at)))


class TestAudit:
    async def test_records_successful_request(
        self, audit_app: FastAPI, connection: AsyncConnection
    ) -> None:
        token = access_token(audit_app, USER_ID)
        async with client_for(audit_app) as client:
            response = await client.patch(
                "/api/v1/audit-probe/items/5",
                json={"displayName": "Ann", "currentPassword": PASSWORD, "unknown": 1},
                headers={
                    **bearer(token),
                    "X-Forwarded-For": "198.51.100.1",
                    "User-Agent": "probe/1.0",
                },
            )
        assert response.status_code == 200
        [log] = await _audit_logs(connection)
        assert (log.action, log.resource_type, log.resource_id, log.actor_id) == (
            "item.update",
            "item",
            "5",
            None,
        )
        assert log.changes == {"displayName": "Ann"}
        assert (str(log.ip), log.user_agent) == ("203.0.113.7", "probe/1.0")
        assert log.request_id == response.headers["x-request-id"]

    async def test_records_actor_of_authenticated_route(
        self, audit_app: FastAPI, connection: AsyncConnection
    ) -> None:
        async with client_for(audit_app) as client:
            tokens = await login(client, USER_EMAIL)
            response = await client.patch(
                "/api/v1/audit-probe/me/items/5",
                json={"displayName": "Ann"},
                headers=bearer(tokens["accessToken"]),
            )
        assert response.status_code == 200
        [log] = await _audit_logs(connection)
        assert (log.resource_id, log.actor_id) == ("5", str(tokens["user"]["id"]))

    async def test_null_changes_are_stored_as_sql_null(
        self, audit_app: FastAPI, connection: AsyncConnection
    ) -> None:
        await write_audit_log(
            audit_app.state.context,
            action="item.delete",
            resource_type="item",
            resource_id="1",
            actor_id=None,
            changes=None,
        )
        assert await _scalar(connection, "SELECT changes IS NULL AND ip IS NULL FROM audit_logs")

    async def test_resource_id_from_response_and_failures_skipped(
        self, audit_app: FastAPI, connection: AsyncConnection
    ) -> None:
        async with client_for(audit_app) as client:
            created = await client.post("/api/v1/audit-probe/items")
            failed = await client.post("/api/v1/audit-probe/fail")
        assert (created.status_code, failed.status_code) == (200, 422)
        [log] = await _audit_logs(connection)
        assert (log.action, log.resource_id, log.actor_id, log.changes) == (
            "item.create",
            "99",
            None,
            None,
        )

    async def test_audit_failure_does_not_affect_response(
        self, audit_app: FastAPI, connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def broken(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("db down")

        monkeypatch.setattr(audit_module, "write_audit_log", broken)
        async with client_for(audit_app) as client:
            response = await client.post("/api/v1/audit-probe/items")
        assert response.status_code == 200
        assert await _audit_logs(connection) == []

    async def test_admin_role_change_is_listed(
        self, audit_app: FastAPI, connection: AsyncConnection
    ) -> None:
        async with client_for(audit_app) as client:
            admin = await login(client, ADMIN_EMAIL)
            user = await login(client, USER_EMAIL)
            headers = bearer(admin["accessToken"])
            forbidden = await client.get("/api/v1/audit-logs", headers=bearer(user["accessToken"]))
            changed = await client.put(
                f"/api/v1/users/{user['user']['id']}/roles",
                json={"roles": ["user", "admin"]},
                headers=headers,
            )
            assert changed.status_code == 200, changed.text
            listed = await client.get("/api/v1/audit-logs?limit=1", headers=headers)
        [item] = listed.json()["data"]["items"]
        assert (item["action"], item["resourceType"], item["resourceId"], item["actorId"]) == (
            "user.setRoles",
            "user",
            user["user"]["id"],
            admin["user"]["id"],
        )
        assert item["changes"] == {"roles": ["user", "admin"]}
        assert forbidden.status_code == 403

    async def test_cursor_without_time_zone_is_rejected(self, audit_app: FastAPI) -> None:
        cursor = encode_cursor("2026-01-01T00:00:00", str(USER_ID))
        async with client_for(audit_app) as client:
            admin = await login(client, ADMIN_EMAIL)
            response = await client.get(
                "/api/v1/audit-logs",
                params={"cursor": cursor},
                headers=bearer(admin["accessToken"]),
            )
        assert response.status_code == 422
        assert response.json()["data"]["errors"][0]["field"] == "query.cursor"


class TestAccountCleanup:
    async def test_delete_account_runs_cleaners_in_process(
        self, committed_app: FastAPI, cleaners: list[tuple[str, UUID]]
    ) -> None:
        @account_cleanup.register_account_cleaner(10)
        class Probe:
            name = "probe"

            def __init__(self, context: AppContext) -> None:
                self.context = context

            async def clean(self, user_id: UUID) -> None:
                cleaners.append(("probe", user_id))

        context: AppContext = committed_app.state.context
        context.queue = TaskQueue.from_settings(context.settings)
        context.queue.bind(context)
        async with client_for(committed_app) as client:
            tokens = await login(client, USER_EMAIL)
            deleted = await client.delete("/api/v1/me", headers=bearer(tokens["accessToken"]))
        assert deleted.status_code == 202
        await context.queue.shutdown()
        user_id = UUID(tokens["user"]["id"])
        assert cleaners == [("probe", user_id)]
        async with context.db.transaction() as session:
            completed = await session.scalar(
                text("SELECT completed_at FROM account_deletions WHERE user_id = :id"),
                {"id": user_id},
            )
            active = await session.scalar(
                text("SELECT count(*) FROM sessions WHERE user_id = :id AND revoked_at IS NULL"),
                {"id": user_id},
            )
        assert completed is not None
        assert active == 0

    async def test_sessions_cleaner_deletes_sessions_and_refresh_tokens(
        self, committed_app: FastAPI
    ) -> None:
        context: AppContext = committed_app.state.context
        async with client_for(committed_app) as client:
            tokens = await login(client, USER_EMAIL)
        user_id = UUID(tokens["user"]["id"])

        await SessionsAccountCleaner(context.db).clean(user_id)
        await SessionsAccountCleaner(context.db).clean(user_id)

        async with context.db.transaction() as session:
            counts = (
                await session.execute(
                    text(
                        "SELECT (SELECT count(*) FROM sessions WHERE user_id = :id), "
                        "(SELECT count(*) FROM refresh_tokens)"
                    ),
                    {"id": user_id},
                )
            ).one()
        assert tuple(counts) == (0, 0)

    async def test_final_failure_logs_error(
        self,
        committed_app: FastAPI,
        cleaners: list[tuple[str, UUID]],
        no_sleep: list[float],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        @account_cleanup.register_account_cleaner(10)
        class Failing:
            name = "files"

            def __init__(self, context: AppContext) -> None:
                pass

            async def clean(self, user_id: UUID) -> None:
                raise RuntimeError("s3 down")

        context: AppContext = committed_app.state.context
        context.queue = TaskQueue.from_settings(context.settings)
        context.queue.bind(context)
        user_id = await _user_id(context.db, USER_EMAIL)
        await _request_deletion(context.db, user_id)
        with caplog.at_level("ERROR", logger="app.account_deletion"):
            await AccountCleanupProducer(context).enqueue_cleanup(user_id)
            await context.queue.shutdown()
        assert no_sleep == [10.0, 20.0, 40.0, 80.0]
        async with context.db.transaction() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT completed_at, attempts, last_error FROM account_deletions "
                        "WHERE user_id = :id"
                    ),
                    {"id": user_id},
                )
            ).one()
        assert (row.completed_at, row.attempts) == (None, 5)
        assert row.last_error == f"Account cleanup for user {user_id} failed: files (s3 down)"
        failures = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith(f"Account cleanup for user {user_id} failed")
        ]
        assert failures == [f"Account cleanup for user {user_id} failed: files (s3 down)"]

    async def test_redis_queue_deduplicates_pending_jobs(
        self, committed_app: FastAPI, redis: Redis, cleaners: list[tuple[str, UUID]]
    ) -> None:
        held: list[int] = []

        @account_cleanup.register_account_cleaner(10)
        class Probe:
            name = "probe"

            def __init__(self, context: AppContext) -> None:
                pass

            async def clean(self, user_id: UUID) -> None:
                cleaners.append(("probe", user_id))
                held.append(await redis.exists(f"api:lock:account-cleanup:{user_id}"))

        app_context: AppContext = committed_app.state.context
        context = make_context(app_context.db, redis)
        queue = context.queue
        assert queue is not None
        await queue.startup()
        user_id = await _user_id(context.db, USER_EMAIL)
        producer = AccountCleanupProducer(context)
        try:
            await producer.enqueue_cleanup(user_id)
            await producer.enqueue_cleanup(user_id)
            assert await redis.xlen(stream_name(SERVICE)) == 1
            assert await redis.exists(pending_key(SERVICE, user_id)) == 1
            assert await redis.pttl(pending_key(SERVICE, user_id)) > 23 * 60 * 60 * 1000
            await consume(queue, 1)
        finally:
            await queue.shutdown()
        assert cleaners == [("probe", user_id)]
        assert held == [1]
        assert await redis.exists(pending_key(SERVICE, user_id)) == 0
        assert await redis.exists(f"api:lock:account-cleanup:{user_id}") == 0

    async def test_skips_user_locked_by_another_worker(
        self, committed_app: FastAPI, redis: Redis, cleaners: list[tuple[str, UUID]]
    ) -> None:
        @account_cleanup.register_account_cleaner(10)
        class Probe:
            name = "probe"

            def __init__(self, context: AppContext) -> None:
                pass

            async def clean(self, user_id: UUID) -> None:
                cleaners.append(("probe", user_id))

        app_context: AppContext = committed_app.state.context
        context = make_context(app_context.db, redis)
        queue = context.queue
        assert queue is not None
        await queue.startup()
        user_id = await _user_id(context.db, USER_EMAIL)
        other_worker = LockService(redis, SERVICE)
        token = await other_worker.try_acquire(f"account-cleanup:{user_id}", 60_000)
        assert token is not None
        try:
            await AccountCleanupProducer(context).enqueue_cleanup(user_id)
            await consume(queue, 1)
        finally:
            await queue.shutdown()
        assert cleaners == []
        assert await redis.exists(pending_key(SERVICE, user_id)) == 1
        assert await redis.get(f"api:lock:account-cleanup:{user_id}") == token.encode()
        await other_worker.release(f"account-cleanup:{user_id}", token)
        await redis.delete(pending_key(SERVICE, user_id))


class FailingPublisher:
    def __init__(self, failures: int) -> None:
        self.failures = failures

    async def publish(self, event: object) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("stream unavailable")


class TestUserDeletedEvent:
    async def test_cleanup_publishes_to_redis_stream_once(
        self,
        committed_app: FastAPI,
        redis: Redis,
        cleaners: list[tuple[str, UUID]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stream = f"events:{uuid7().hex}"
        monkeypatch.setenv("EVENTS_TRANSPORT", "redis_stream")
        monkeypatch.setenv("EVENTS_REDIS_STREAM", stream)
        app = _build_app(committed_app.state.context.db, redis=redis)
        context: AppContext = app.state.context
        async with client_for(app) as client:
            tokens = await login(client, USER_EMAIL)
            deleted = await client.delete("/api/v1/me", headers=bearer(tokens["accessToken"]))
        assert deleted.status_code == 202
        assert await redis.xlen(stream) == 0

        user_id = UUID(tokens["user"]["id"])
        runner = AccountCleanupRunner.from_context(context)
        await runner.run(user_id)
        await runner.run(user_id)

        entries: Any = await redis.xrange(stream)
        [(_, fields)] = entries
        event = decode(fields[b"event"])
        payload = json.loads(fields[b"event"])
        assert payload["type"] == USER_DELETED
        assert payload["source"] == "/api"
        assert event.get_data() == {"userId": str(user_id)}
        async with context.db.transaction() as session:
            published = await session.scalar(
                text("SELECT event_published_at FROM account_deletions WHERE user_id = :id"),
                {"id": user_id},
            )
        assert published is not None

    async def test_failed_publish_is_recorded_and_retried(
        self, committed_app: FastAPI, cleaners: list[tuple[str, UUID]]
    ) -> None:
        context: AppContext = committed_app.state.context
        user_id = await _user_id(context.db, USER_EMAIL)
        await _request_deletion(context.db, user_id)

        @account_cleanup.register_account_cleaner(10)
        class Probe:
            name = "probe"

            def __init__(self, context: AppContext) -> None:
                self.context = context

            async def clean(self, user_id: UUID) -> None:
                cleaners.append(("probe", user_id))

        runner = AccountCleanupRunner(
            context.db, account_cleanup.registered_cleaners(context), FailingPublisher(1), "api"
        )
        with pytest.raises(RuntimeError, match="stream unavailable"):
            await runner.run(user_id)
        assert cleaners == []
        async with context.db.transaction() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT event_published_at, completed_at, last_error "
                        "FROM account_deletions WHERE user_id = :id"
                    ),
                    {"id": user_id},
                )
            ).one()
        assert (row[0], row[1], row[2]) == (None, None, "stream unavailable")

        await runner.run(user_id)
        assert cleaners == [("probe", user_id)]


class TestLifespan:
    async def test_shutdown_waits_for_pending_mail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAIL_HOST", "127.0.0.1")
        get_settings.cache_clear()
        app = create_app(Settings.load())
        context: AppContext = app.state.context
        mailer = context.mailer
        assert isinstance(mailer, QueueMailer)
        finished: list[str] = []
        finished_before_dispose: list[str] = []
        dispose = context.db.dispose

        async def recording_dispose() -> None:
            finished_before_dispose.extend(finished)
            await dispose()

        monkeypatch.setattr(context.db, "dispose", recording_dispose)

        async def slow(name: str) -> None:
            await asyncio.sleep(0.3)
            finished.append(name)

        async with app.router.lifespan_context(app):
            mail_task = asyncio.create_task(slow("mail"))
            mailer.pending.add(mail_task)
        assert finished_before_dispose == ["mail"]


class TestScheduler:
    async def test_cleanup_conditions(self, committed_app: FastAPI) -> None:
        context: AppContext = committed_app.state.context
        user_id = await _user_id(context.db, USER_EMAIL)
        async with context.db.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO sessions (user_id, user_agent, auth_time, expires_at, revoked_at) "
                    "VALUES "
                    "(:u, 'expired', now(), now() - INTERVAL '1 minute', NULL), "
                    "(:u, 'old', now(), now() + INTERVAL '1 day', now() - INTERVAL '8 days'), "
                    "(:u, 'recent', now(), now() + INTERVAL '1 day', now() - INTERVAL '6 days'), "
                    "(:u, 'active', now(), now() + INTERVAL '1 day', NULL)"
                ),
                {"u": user_id},
            )
            await session.execute(
                text(
                    "INSERT INTO verification_tokens (user_id, purpose, token_hash, expires_at) "
                    "VALUES (:u, 'verify_email', 'expired', now() - INTERVAL '1 minute'), "
                    "(:u, 'reset_password', 'valid', now() + INTERVAL '1 minute')"
                ),
                {"u": user_id},
            )

        context.queue = TaskQueue.from_settings(context.settings)
        context.queue.bind(context)
        token = await context.locks.try_acquire("cron:cleanup-old-sessions", 60_000)
        assert token is not None
        await context.queue.enqueue(cleanup_old_sessions_task)
        await context.queue.shutdown()
        async with context.db.transaction() as session:
            assert await session.scalar(text("SELECT count(*) FROM sessions")) == 4
        await context.locks.release("cron:cleanup-old-sessions", token)

        await context.queue.enqueue(cleanup_old_sessions_task)
        await context.queue.shutdown()
        assert await cleanup_old_sessions(context) == 0
        async with context.db.transaction() as session:
            agents = await session.scalars(text("SELECT user_agent FROM sessions"))
            assert sorted(agents) == ["active", "recent"]
            hashes = await session.scalars(text("SELECT token_hash FROM verification_tokens"))
            assert list(hashes) == ["valid"]

    async def test_compensation_enqueues_users_deleted_before_grace(
        self, committed_app: FastAPI, cleaners: list[tuple[str, UUID]]
    ) -> None:
        @account_cleanup.register_account_cleaner(10)
        class Probe:
            name = "probe"

            def __init__(self, context: AppContext) -> None:
                pass

            async def clean(self, user_id: UUID) -> None:
                cleaners.append(("probe", user_id))

        context: AppContext = committed_app.state.context
        context.queue = TaskQueue.from_settings(context.settings)
        context.queue.bind(context)
        old_id = await _user_id(context.db, USER_EMAIL)
        new_id = await _user_id(context.db, "admin@example.com")
        await _request_deletion(context.db, old_id, minutes_ago=31)
        await _request_deletion(context.db, new_id, minutes_ago=10)
        assert await enqueue_pending_account_cleanups(context) == 1
        await context.queue.shutdown()
        assert cleaners == [("probe", old_id)]
        assert await enqueue_pending_account_cleanups(context) == 0

    async def test_schedules(self) -> None:
        queue = TaskQueue.from_settings(Settings.load())
        scheduler = create_scheduler(queue, Settings.load())
        [source] = scheduler.sources
        await source.startup()
        schedules = {
            item.task_name: (item.cron, item.cron_offset) for item in await source.get_schedules()
        }
        assert schedules == {
            "scheduler:cleanup-old-sessions": ("0 3 * * *", "UTC"),
            "scheduler:account-cleanup-compensation": ("25 * * * *", "Asia/Shanghai"),
        }
