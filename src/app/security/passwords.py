"""约定 §5.4 密码：argon2id（m=19456 KiB, t=2, p=1），哈希在线程池中执行并限制并发。"""

import weakref
from asyncio import AbstractEventLoop, get_running_loop
from dataclasses import dataclass
from typing import Final
lazy from collections.abc import Callable

import anyio
import anyio.to_thread
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

ARGON2_TIME_COST: Final = 2
ARGON2_MEMORY_COST_KIB: Final = 19_456
ARGON2_PARALLELISM: Final = 1
PASSWORD_MIN_LENGTH: Final = 8
PASSWORD_MAX_LENGTH: Final = 128


@dataclass(frozen=True, slots=True)
class VerifyResult:
    valid: bool
    updated_hash: str | None


class Passwords:
    def __init__(self, *, concurrency: int = 2) -> None:
        self._hash = PasswordHash(
            (
                Argon2Hasher(
                    time_cost=ARGON2_TIME_COST,
                    memory_cost=ARGON2_MEMORY_COST_KIB,
                    parallelism=ARGON2_PARALLELISM,
                ),
            )
        )
        self._dummy = self._hash.hash("dummy-password-for-timing")
        self._concurrency = concurrency
        self._limiters: weakref.WeakKeyDictionary[AbstractEventLoop, anyio.CapacityLimiter] = (
            weakref.WeakKeyDictionary()
        )

    def hash(self, password: str) -> str:
        return self._hash.hash(password)

    def verify(self, password: str, password_hash: str | None) -> VerifyResult:
        """`password_hash` 为空时校验固定假哈希并返回失败；参数变化时返回新哈希。"""
        try:
            valid, updated = self._hash.verify_and_update(password, password_hash or self._dummy)
        except Exception:
            return VerifyResult(valid=False, updated_hash=None)
        if password_hash is None:
            return VerifyResult(valid=False, updated_hash=None)
        return VerifyResult(valid=valid, updated_hash=updated if valid else None)

    async def hash_async(self, password: str) -> str:
        return await self._run(self.hash, password)

    async def verify_async(self, password: str, password_hash: str | None) -> VerifyResult:
        return await self._run(self.verify, password, password_hash)

    async def _run[**P, T](self, func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        loop = get_running_loop()
        limiter = self._limiters.get(loop)
        if limiter is None:
            limiter = self._limiters[loop] = anyio.CapacityLimiter(self._concurrency)
        return await anyio.to_thread.run_sync(lambda: func(*args, **kwargs), limiter=limiter)
