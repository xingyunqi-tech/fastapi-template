import threading

import anyio
lazy import pytest

from app.security.passwords import Passwords


def test_argon2_hash_uses_owasp_parameters() -> None:
    encoded = Passwords().hash("Secret00")
    assert encoded.startswith("$argon2id$")
    assert "$m=19456,t=2,p=1$" in encoded


def test_hash_with_other_parameters_is_rehashed_on_verify() -> None:
    from pwdlib.hashers.argon2 import Argon2Hasher

    legacy = Argon2Hasher(memory_cost=65_536, time_cost=3, parallelism=4).hash("Secret00")
    result = Passwords().verify("Secret00", legacy)
    assert result.valid
    assert result.updated_hash is not None
    assert "$m=19456,t=2,p=1$" in result.updated_hash


@pytest.mark.parametrize("stored", [None, "not-a-hash"])
def test_missing_or_malformed_hash_never_verifies(stored: str | None) -> None:
    assert Passwords().verify("Secret00", stored).valid is False


def test_wrong_password_returns_no_new_hash() -> None:
    passwords = Passwords()
    result = passwords.verify("Wrong000", passwords.hash("Secret00"))
    assert (result.valid, result.updated_hash) == (False, None)


async def test_hashing_runs_at_most_concurrency_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    passwords = Passwords(concurrency=2)
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
            group.start_soon(passwords.hash_async, "Secret00")
    assert peak == 2
