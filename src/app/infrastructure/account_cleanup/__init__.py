"""注销账号后的数据清理：清理器登记与执行、`account_deletions` 进度记录、失败日志、入队。"""

import asyncio
import logging
from collections.abc import Callable
from typing import Annotated, Any, Final, Protocol
lazy from datetime import datetime
lazy from uuid import UUID

from sqlalchemy import text
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.queue import (
    ACCOUNT_CLEANUP_QUEUE,
    APP_CONTEXT,
    RetryPolicy,
    run_job,
    stream_name,
    tasks,
)
lazy from app.common.context import AppContext
lazy from app.infrastructure.database.session import Database

logger = logging.getLogger("app.account_cleanup")

CLEAN_ACCOUNT_TASK: Final = "account-cleanup:clean-account"
ACCOUNT_CLEANUP_RETRY: Final = RetryPolicy(attempts=5, delay_ms=10_000)
PENDING_KEY_TTL_MS: Final = 24 * 60 * 60 * 1000
RUN_LOCK_TTL_MS: Final = 60 * 60 * 1000


class AccountDataCleaner(Protocol):
    """注销账号后清理某一类用户数据，clean 须幂等。"""

    @property
    def name(self) -> str: ...

    async def clean(self, user_id: UUID) -> None: ...


type CleanerFactory = Callable[[AppContext], AccountDataCleaner]

_CLEANERS: list[tuple[int, CleanerFactory]] = []


def register_account_cleaner[F: CleanerFactory](order: int) -> Callable[[F], F]:
    """登记清理器工厂（接收 AppContext），按 order 升序执行；同一工厂只登记一次。"""

    def decorator(factory: F) -> F:
        if all(existing is not factory for _, existing in _CLEANERS):
            _CLEANERS.append((order, factory))
        return factory

    return decorator


def registered_cleaners(context: AppContext) -> list[AccountDataCleaner]:
    return [factory(context) for _, factory in sorted(_CLEANERS, key=lambda item: item[0])]


def pending_key(user_id: UUID) -> str:
    return f"{stream_name(ACCOUNT_CLEANUP_QUEUE)}:user-{user_id}"


def run_lock_key(user_id: UUID) -> str:
    return f"{ACCOUNT_CLEANUP_QUEUE}:user-{user_id}"


class AccountCleanupError(Exception):
    def __init__(self, user_id: UUID, failed_cleaners: list[str], causes: list[BaseException]):
        details = ", ".join(
            f"{name} ({cause})" for name, cause in zip(failed_cleaners, causes, strict=True)
        )
        super().__init__(f"Account cleanup for user {user_id} failed: {details}")
        self.user_id = user_id
        self.failed_cleaners = failed_cleaners
        self.causes = causes


def log_account_cleanup_failure(user_id: UUID, error: BaseException) -> None:
    if isinstance(error, AccountCleanupError):
        logger.error("%s", error)
    else:
        logger.error("Account cleanup for user %s failed: %s", user_id, error)


class AccountCleanupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def mark_completed(self, user_id: UUID) -> None:
        await self.session.execute(
            text(
                "UPDATE account_deletions SET completed_at = now(), "
                "attempts = attempts + 1, last_error = NULL WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )

    async def mark_failed(self, user_id: UUID, error: str) -> None:
        await self.session.execute(
            text(
                "UPDATE account_deletions SET attempts = attempts + 1, last_error = :error "
                "WHERE user_id = :user_id"
            ),
            {"user_id": user_id, "error": error},
        )

    async def find_pending_user_ids(self, requested_before: datetime, limit: int) -> list[UUID]:
        """注销请求早于 requested_before 且尚未完成清理的用户，按用户 id 升序。"""
        result = await self.session.execute(
            text(
                "SELECT user_id FROM account_deletions "
                "WHERE completed_at IS NULL AND requested_at < :requested_before "
                "ORDER BY user_id LIMIT :limit"
            ),
            {"requested_before": requested_before, "limit": limit},
        )
        return [row[0] for row in result]


class AccountCleanupRunner:
    """依次执行全部清理器，单个失败不影响其余清理器，全部执行后汇总抛出；全部成功时记录完成时间。"""

    def __init__(
        self,
        database: Database,
        cleaners: list[AccountDataCleaner],
    ) -> None:
        self.database = database
        self.cleaners = cleaners

    @classmethod
    def from_context(cls, context: AppContext) -> AccountCleanupRunner:
        return cls(context.database, registered_cleaners(context))

    @property
    def cleaner_names(self) -> list[str]:
        return [cleaner.name for cleaner in self.cleaners]

    async def run(self, user_id: UUID) -> None:
        failed: list[str] = []
        causes: list[BaseException] = []
        for cleaner in self.cleaners:
            try:
                await cleaner.clean(user_id)
            except Exception as error:
                failed.append(cleaner.name)
                causes.append(error)
                logger.exception("Cleaner %s failed for user %s", cleaner.name, user_id)
        if failed:
            failure = AccountCleanupError(user_id, failed, causes)
            async with self.database.transaction() as session:
                await AccountCleanupRepository(session).mark_failed(user_id, str(failure))
            raise failure
        async with self.database.transaction() as session:
            await AccountCleanupRepository(session).mark_completed(user_id)
        logger.info("Account data of user %s cleaned", user_id)

    async def report_failure(self, user_id: UUID, error: BaseException) -> None:
        log_account_cleanup_failure(user_id, error)


_serial = asyncio.Lock()


@tasks.task(task_name=CLEAN_ACCOUNT_TASK, queue_name=stream_name(ACCOUNT_CLEANUP_QUEUE))
async def clean_account(
    job: dict[str, Any],
    context: Annotated[AppContext, APP_CONTEXT],
) -> None:
    """持 `lock:account-cleanup:user-<id>` 执行，锁被占用时跳过；失败重试，最终失败记日志。"""
    user_id = UUID(job["userId"])
    runner = AccountCleanupRunner.from_context(context)
    async with _serial:
        token = await context.locks.try_acquire(run_lock_key(user_id), RUN_LOCK_TTL_MS)
        if token is None:
            logger.info("Account cleanup for user %s is running elsewhere, skipped", user_id)
            return
        try:
            await run_job(
                ACCOUNT_CLEANUP_QUEUE,
                ACCOUNT_CLEANUP_RETRY,
                lambda: runner.run(user_id),
                on_final_failure=lambda error: runner.report_failure(user_id, error),
            )
        finally:
            await context.locks.release(run_lock_key(user_id), token)
            if context.redis is not None:
                await context.redis.delete(pending_key(user_id))


class AccountCleanupProducer:
    """账号清理任务入队，入队失败时记日志；去重键存在（任务排队或执行中）时不再入队，任务结束时删除去重键。"""

    def __init__(self, context: AppContext) -> None:
        self.context = context

    async def enqueue_cleanup(self, user_id: UUID) -> None:
        queue = self.context.queue
        if queue is None:
            logger.warning("No account cleanup runner for user %s", user_id)
            return
        redis = self.context.redis if queue.distributed else None
        try:
            if redis is not None and not await redis.set(
                pending_key(user_id), "1", px=PENDING_KEY_TTL_MS, nx=True
            ):
                return
            try:
                await queue.enqueue(clean_account, {"userId": str(user_id)})
            except BaseException:
                if redis is not None:
                    await redis.delete(pending_key(user_id))
                raise
        except Exception as error:
            log_account_cleanup_failure(user_id, error)
