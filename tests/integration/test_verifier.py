import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx as httpx_lib
import pytest
from alembic import command
from alembic.config import Config
from cloudevents.core.v1.event import CloudEvent
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.factory import create_app
from app.identity.account_deletion.cleanup import AccountCleanupRunner
from app.infrastructure.events import (
    STREAM_FIELD,
    USER_DELETED,
    RedisStreamPublisher,
    encode,
    user_deleted,
)
from app.infrastructure.events.consumer import EventConsumer, build_consumer
from app.modules.notes.models import NoteModel
from tests.conftest import use_verifier
from tests.integration.conftest import ROOT, PgServer, _execute, client_for, error_of
from tests.integration.identity import USER_EMAIL, bearer, login
lazy from app.common.context import AppContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

VERIFIER_SCHEMA = "notes_service"


def _paths(app: FastAPI) -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


class TestVerifierWithoutDatabase:
    async def test_mounts_only_stateless_routes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_verifier(monkeypatch)
        app = create_app(Settings.load())
        context: AppContext = app.state.context
        assert (context.database, context.signer, context.file_urls) == (None, None, None)
        assert not any(path.startswith("/api/") for path in _paths(app))
        assert "/.well-known/jwks.json" not in _paths(app)

        async with client_for(app) as client:
            assert (await client.get("/health/ready")).status_code == 200
            assert error_of(await client.get("/api/v1/notes")) == (404, "common.notFound")
            assert build_consumer(context) is None


@pytest.fixture
async def verifier_db(pg_server: PgServer, monkeypatch: pytest.MonkeyPatch) -> str:
    """只升级 platform + modules 分支的独立库。"""
    name = f"verifier_{uuid.uuid4().hex[:12]}"
    await _execute(pg_server.dsn("postgres"), f'CREATE DATABASE "{name}"')
    monkeypatch.setenv("DATABASE_URL", pg_server.url(name))
    monkeypatch.setenv("DATABASE_SCHEMA", VERIFIER_SCHEMA)
    config = Config(os.path.join(ROOT, "alembic.ini"))
    await asyncio.to_thread(command.upgrade, config, "modules@head")
    return name


@pytest.fixture
async def verifier(
    committed_app: FastAPI,
    verifier_db: str,
    pg_server: PgServer,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FastAPI]:
    """用 `committed_app` 的 JWKS 与 Redis 的验证方，`user.deleted` 走独立 stream。"""
    use_verifier(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", pg_server.url(verifier_db))
    monkeypatch.setenv("DATABASE_SCHEMA", VERIFIER_SCHEMA)
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("OTEL_SERVICE_NAME", "notes-service")
    monkeypatch.setenv("EVENTS_TRANSPORT", "redis_stream")
    monkeypatch.setenv("EVENTS_REDIS_STREAM", f"events:{uuid.uuid4().hex}")
    get_settings.cache_clear()
    app = create_app(Settings.load())
    context: AppContext = app.state.context
    assert context.redis is not None
    async for key in context.redis.scan_iter("rl:*"):
        await context.redis.delete(key)
    assert context.verifier.jwks is not None
    context.verifier.jwks.transport = httpx_lib.ASGITransport(app=committed_app)
    yield app
    await context.db.dispose()
    if context.redis is not None:
        await context.redis.aclose()


class TestVerifierWithDatabase:
    async def test_mounts_notes_and_audit_without_identity_or_files(
        self, verifier: FastAPI
    ) -> None:
        paths = _paths(verifier)
        assert {"/api/v1/notes", "/api/v1/audit-logs"} <= paths
        assert not any(
            path.startswith(("/api/v1/auth", "/api/v1/me", "/api/v1/files")) for path in paths
        )
        async with client_for(verifier) as client:
            assert (await client.get("/health/ready")).status_code == 200

    async def test_issuer_tokens_and_user_deleted_events(
        self, verifier: FastAPI, committed_app: FastAPI
    ) -> None:
        context: AppContext = verifier.state.context
        issuer: AppContext = committed_app.state.context
        async with client_for(committed_app) as issuer_client, client_for(verifier) as client:
            tokens = await login(issuer_client, USER_EMAIL)
            headers = bearer(tokens["accessToken"])
            created = await client.post("/api/v1/notes", json={"title": "kept"}, headers=headers)
            assert created.status_code == 201, created.text

            deleted = await issuer_client.delete("/api/v1/me", headers=headers)
            assert deleted.status_code == 202
            assert error_of(await client.get("/api/v1/notes", headers=headers)) == (
                401,
                "common.unauthorized",
            )

        user_id = UUID(tokens["user"]["id"])
        stream = context.settings.events.redis_stream
        assert issuer.redis is not None
        assert stream is not None
        runner = AccountCleanupRunner(
            issuer.db, [], RedisStreamPublisher(issuer.redis, stream), "api"
        )
        await runner.run(user_id)

        consumer = build_consumer(context)
        assert consumer is not None
        await consumer.ensure_group()
        assert await consumer.poll(block_ms=None) == 1
        async with context.db.transaction() as session:
            remaining = await session.scalar(
                select(func.count())
                .select_from(NoteModel)
                .where(NoteModel.owner_id == str(user_id))
            )
        assert remaining == 0
        assert await consumer.redis.xpending(stream, consumer.group) == {
            "pending": 0,
            "min": None,
            "max": None,
            "consumers": [],
        }


@pytest.fixture
async def stream_redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url)
    yield client
    await client.aclose()


class Recorder:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.events: list[str] = []
        self.updated = asyncio.Event()

    async def __call__(self, event: CloudEvent) -> None:
        self.events.append(event.get_id())
        self.updated.set()
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("handler failed")


def consumer_of(redis: Redis, stream: str, handler: Recorder, **options: Any) -> EventConsumer:
    return EventConsumer(redis, stream, "notes-service", {USER_DELETED: handler}, **options)


class TestEventConsumer:
    async def test_acks_handled_unknown_and_malformed_messages(self, stream_redis: Redis) -> None:
        stream = f"events:{uuid.uuid4().hex}"
        handler = Recorder()
        consumer = consumer_of(stream_redis, stream, handler)
        await consumer.ensure_group()
        await consumer.ensure_group()
        event = user_deleted("api", str(UUID(int=1)))
        other = CloudEvent({"id": "o", "type": "user.created", "source": "/api"}, None)
        await stream_redis.xadd(stream, {STREAM_FIELD: encode(event)})
        await stream_redis.xadd(stream, {STREAM_FIELD: encode(other)})
        await stream_redis.xadd(stream, {STREAM_FIELD: b"not json"})
        await stream_redis.xadd(stream, {"other": b"x"})

        assert await consumer.poll(block_ms=None) == 4
        assert handler.events == [event.get_id()]
        pending: Any = await stream_redis.xpending(stream, consumer.group)
        assert pending["pending"] == 0

    async def test_failed_messages_are_retried_then_dropped(
        self, stream_redis: Redis, caplog: pytest.LogCaptureFixture
    ) -> None:
        stream = f"events:{uuid.uuid4().hex}"
        handler = Recorder(failures=10)
        consumer = consumer_of(stream_redis, stream, handler, max_deliveries=3, min_idle_ms=0)
        await consumer.ensure_group()
        event = user_deleted("api", str(UUID(int=1)))
        await stream_redis.xadd(stream, {STREAM_FIELD: encode(event)})

        with caplog.at_level(logging.ERROR, logger="app.events"):
            for _ in range(4):
                await consumer.poll(block_ms=None)
        assert handler.events == [event.get_id()] * 3
        pending: Any = await stream_redis.xpending(stream, consumer.group)
        assert pending["pending"] == 0
        assert any("dropped after 3 deliveries" in r.getMessage() for r in caplog.records)

    async def test_retried_message_is_acked_after_success(self, stream_redis: Redis) -> None:
        stream = f"events:{uuid.uuid4().hex}"
        handler = Recorder(failures=1)
        consumer = consumer_of(stream_redis, stream, handler, min_idle_ms=0)
        await consumer.ensure_group()
        await stream_redis.xadd(stream, {STREAM_FIELD: encode(user_deleted("api", "x"))})

        await consumer.poll(block_ms=None)
        pending: Any = await stream_redis.xpending(stream, consumer.group)
        assert pending["pending"] == 1
        await consumer.poll(block_ms=None)
        pending = await stream_redis.xpending(stream, consumer.group)
        assert pending["pending"] == 0
        assert len(handler.events) == 2

    async def test_recreates_a_deleted_group_and_replays_the_stream(
        self, stream_redis: Redis
    ) -> None:
        stream = f"events:{uuid.uuid4().hex}"
        handler = Recorder()
        consumer = consumer_of(stream_redis, stream, handler, block_ms=100)
        task = asyncio.create_task(consumer.run())
        try:
            first = user_deleted("api", str(UUID(int=1)))
            await stream_redis.xadd(stream, {STREAM_FIELD: encode(first)})
            await wait_for_events(handler, 1)
            await stream_redis.xgroup_destroy(stream, consumer.group)
            second = user_deleted("api", str(UUID(int=2)))
            await stream_redis.xadd(stream, {STREAM_FIELD: encode(second)})
            await wait_for_events(handler, 3)
            assert handler.events == [first.get_id(), first.get_id(), second.get_id()]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def wait_for_events(handler: Recorder, count: int) -> None:
    async with asyncio.timeout(10):
        while len(handler.events) < count:
            handler.updated.clear()
            await handler.updated.wait()
