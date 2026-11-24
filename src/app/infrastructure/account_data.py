"""注销账号后的本地数据清理器：签发方的清理任务与验证方的 `user.deleted` 消费者共用。"""

import logging
from collections.abc import Callable
from typing import Protocol
lazy from uuid import UUID

lazy from app.common.context import AppContext

logger = logging.getLogger("app.account_deletion")


class AccountDataCleaner(Protocol):
    """注销账号后清理某一类用户数据，clean 须幂等。"""

    @property
    def name(self) -> str: ...

    async def clean(self, user_id: UUID) -> None: ...


type CleanerFactory = Callable[[AppContext], AccountDataCleaner | None]

_CLEANERS: list[tuple[int, CleanerFactory]] = []


def register_account_cleaner[F: CleanerFactory](order: int) -> Callable[[F], F]:
    """登记清理器工厂，按 order 升序执行；工厂返回 None 表示本服务未启用该数据。"""

    def decorator(factory: F) -> F:
        if all(existing is not factory for _, existing in _CLEANERS):
            _CLEANERS.append((order, factory))
        return factory

    return decorator


def registered_cleaners(context: AppContext) -> list[AccountDataCleaner]:
    cleaners = (factory(context) for _, factory in sorted(_CLEANERS, key=lambda item: item[0]))
    return [cleaner for cleaner in cleaners if cleaner is not None]


class AccountCleanupError(Exception):
    def __init__(self, user_id: UUID, failed_cleaners: list[str], causes: list[BaseException]):
        details = ", ".join(
            f"{name} ({cause})" for name, cause in zip(failed_cleaners, causes, strict=True)
        )
        super().__init__(f"Account cleanup for user {user_id} failed: {details}")
        self.user_id = user_id
        self.failed_cleaners = failed_cleaners
        self.causes = causes


async def run_cleaners(cleaners: list[AccountDataCleaner], user_id: UUID) -> None:
    """依次执行全部清理器，单个失败不影响其余清理器，全部执行后汇总抛出 `AccountCleanupError`。"""
    failed: list[str] = []
    causes: list[BaseException] = []
    for cleaner in cleaners:
        try:
            await cleaner.clean(user_id)
        except Exception as error:
            failed.append(cleaner.name)
            causes.append(error)
            logger.exception("Cleaner %s failed for user %s", cleaner.name, user_id)
    if failed:
        raise AccountCleanupError(user_id, failed, causes)
