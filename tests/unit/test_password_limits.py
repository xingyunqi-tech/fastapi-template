import threading

import anyio
import pytest
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.common.security import Passwords
from app.config import DatabaseSettings, PasswordHasher, Settings
from app.infrastructure.database.session import Database

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
