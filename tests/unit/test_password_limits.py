import importlib
import sys
import threading

import anyio
import pytest
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.common.security import Passwords
from app.config import DatabaseSettings, PasswordHasher, Settings, SettingsError, get_settings
from app.factory import build_context
from app.infrastructure.database.session import Database, _connect_args

MD5 = "e10adc3949ba59abbe56e057f20f883e"


def test_argon2_hash_uses_owasp_parameters() -> None:
    encoded = Passwords(PasswordHasher.ARGON2ID).hash(MD5)
    assert "$m=19456,t=2,p=1$" in encoded


def test_argon2_hash_with_other_parameters_is_rehashed_on_verify() -> None:
    from pwdlib.hashers.argon2 import Argon2Hasher

    legacy = Argon2Hasher(memory_cost=65_536, time_cost=3, parallelism=4).hash(MD5)
    result = Passwords(PasswordHasher.ARGON2ID).verify_md5(MD5, legacy)
    assert result.valid
    assert result.updated_hash is not None
    assert "$m=19456,t=2,p=1$" in result.updated_hash


async def test_hashing_runs_at_most_concurrency_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    passwords = Passwords(PasswordHasher.ARGON2ID, concurrency=2)
    lock = threading.Lock()
    active = peak = 0

    def slow_hash(_: str) -> str:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        threading.Event().wait(0.05)
        with lock:
            active -= 1
        return "hashed"

    monkeypatch.setattr(passwords, "hash", slow_hash)
    async with anyio.create_task_group() as group:
        for _ in range(6):
            group.start_soon(passwords.hash_async, MD5)
    assert peak == 2


@pytest.mark.parametrize(
    ("max_connections", "processes", "pool_size", "overflow"),
    [(100, 1, 20, 80), (100, 2, 20, 30), (100, 8, 12, 0), (3, 4, 1, 0)],
)
def test_pool_splits_max_connections_across_processes(
    max_connections: int, processes: int, pool_size: int, overflow: int
) -> None:
    settings: DatabaseSettings = Settings.load().database.model_copy(
        update={"max_connections": max_connections}
    )
    engine = Database.from_settings(settings, processes=processes).engine
    assert engine is not None
    pool = engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool.size() == pool_size
    assert pool._max_overflow == overflow


def _pool(database: Database) -> AsyncAdaptedQueuePool:
    assert database.engine is not None
    pool = database.engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    return pool


def test_connection_limits_default_to_80_for_api_and_10_for_worker() -> None:
    database = Settings.load().database
    assert database.max_connections == 80
    assert database.worker_max_connections == 10


def test_worker_limit_reads_worker_database_max_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_DATABASE_MAX_CONNECTIONS", "6")
    assert Settings.load().database.worker_max_connections == 6


async def test_api_context_splits_database_max_connections_by_web_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    context = build_context(Settings.load())
    pool = _pool(context.database)
    assert (pool.size(), pool._max_overflow) == (20, 0)
    await context.database.dispose()


async def test_context_with_explicit_limit_ignores_web_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    settings = Settings.load()
    context = build_context(settings, max_connections=settings.database.worker_max_connections)
    pool = _pool(context.database)
    assert (pool.size(), pool._max_overflow) == (10, 0)
    await context.database.dispose()


async def test_worker_process_uses_worker_database_max_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_ENABLED", "true")
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    get_settings.cache_clear()
    monkeypatch.delitem(sys.modules, "app.infrastructure.queue.worker", raising=False)
    worker = importlib.import_module("app.infrastructure.queue.worker")
    pool = _pool(worker.context.database)
    assert (pool.size(), pool._max_overflow) == (10, 0)
    await worker.context.database.dispose()
    await worker.context.redis.aclose()


def test_connection_timeout_defaults_to_10000_ms() -> None:
    assert Settings.load().database.connection_timeout_ms == 10000


@pytest.mark.parametrize("value", ["0", "-1"])
def test_connection_timeout_rejects_non_positive_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DATABASE_CONNECTION_TIMEOUT_MS", value)
    with pytest.raises(SettingsError, match="DATABASE_CONNECTION_TIMEOUT_MS"):
        Settings.load()


async def test_pool_and_connect_timeouts_follow_database_connection_timeout_ms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_CONNECTION_TIMEOUT_MS", "2500")
    settings = Settings.load().database
    database = Database.from_settings(settings)
    assert _pool(database)._timeout == 2.5
    assert _connect_args(settings)["timeout"] == 2.5
    await database.dispose()
