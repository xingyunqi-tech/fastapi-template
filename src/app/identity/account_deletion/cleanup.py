"""注销后的数据清理：发布 `user.deleted`、执行清理器、记录 `account_deletions` 进度、入队。"""

import asyncio
import logging
from typing import Annotated, Any, Final
lazy from datetime import datetime
lazy from uuid import UUID

from sqlalchemy import text
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.account_data import (
    AccountCleanupError,
    AccountDataCleaner,
    registered_cleaners,
    run_cleaners,
)
from app.infrastructure.events import EventPublisher, user_deleted
from app.infrastructure.queue import APP_CONTEXT, RetryPolicy, run_job, tasks
from app.infrastructure.redis import key as redis_key
lazy from app.common.context import AppContext
lazy from app.infrastructure.database.session import Database

logger = logging.getLogger("app.account_deletion")

CLEAN_ACCOUNT_TASK: Final = "account-deletion:clean-account"
CLEANUP_JOB: Final = "account-cleanup"
ACCOUNT_CLEANUP_RETRY: Final = RetryPolicy(attempts=5, delay_ms=10_000)
PENDING_KEY_TTL_MS: Final = 24 * 60 * 60 * 1000
RUN_LOCK_TTL_MS: Final = 60 * 60 * 1000


def pending_key(service: str, user_id: UUID) -> str:
    """任务排队或执行中的去重键。"""
    return redis_key(service, "account-cleanup", "pending", str(user_id))


def run_lock_key(user_id: UUID) -> str:
    return f"account-cleanup:{user_id}"


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

    async def event_published(self, user_id: UUID) -> bool:
        result = await self.session.execute(
            text(
                "SELECT event_published_at IS NOT NULL FROM account_deletions WHERE user_id = :id"
            ),
            {"id": user_id},
        )
        return bool(result.scalar())

    async def mark_event_published(self, user_id: UUID) -> None:
        await self.session.execute(
            text("UPDATE account_deletions SET event_published_at = now() WHERE user_id = :id"),
            {"id": user_id},
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
    """先发布尚未发布的 `user.deleted`，再执行全部清理器；成功记录完成时间，失败记录错误后抛出。"""

    def __init__(
        self,
        database: Database,
        cleaners: list[AccountDataCleaner],
        events: EventPublisher,
        service: str,
    ) -> None:
        self.database = database
        self.cleaners = cleaners
        self.events = events
        self.service = service

    @classmethod
    def from_context(cls, context: AppContext) -> AccountCleanupRunner:
        return cls(
            context.db,
            registered_cleaners(context),
            context.events,
            context.settings.telemetry.service_name,
        )

    @property
    def cleaner_names(self) -> list[str]:
        return [cleaner.name for cleaner in self.cleaners]

    async def run(self, user_id: UUID) -> None:
        try:
            await self._publish(user_id)
            await run_cleaners(self.cleaners, user_id)
        except Exception as failure:
            async with self.database.transaction() as session:
                await AccountCleanupRepository(session).mark_failed(user_id, str(failure))
            raise
        async with self.database.transaction() as session:
            await AccountCleanupRepository(session).mark_completed(user_id)
        logger.info("Account data of user %s cleaned", user_id)

    async def _publish(self, user_id: UUID) -> None:
        async with self.database.transaction() as session:
            if await AccountCleanupRepository(session).event_published(user_id):
                return
        await self.events.publish(user_deleted(self.service, str(user_id)))
        async with self.database.transaction() as session:
            await AccountCleanupRepository(session).mark_event_published(user_id)

    async def report_failure(self, user_id: UUID, error: BaseException) -> None:
        log_account_cleanup_failure(user_id, error)


_serial = asyncio.Lock()


@tasks.task(task_name=CLEAN_ACCOUNT_TASK)
async def clean_account(
    job: dict[str, Any],
    context: Annotated[AppContext, APP_CONTEXT],
) -> None:
    """持 `{service}:lock:account-cleanup:<id>` 执行，锁被占用时跳过；失败重试，最终失败记日志。"""
    user_id = UUID(job["userId"])
    runner = AccountCleanupRunner.from_context(context)
    async with _serial:
        token = await context.locks.try_acquire(run_lock_key(user_id), RUN_LOCK_TTL_MS)
        if token is None:
            logger.info("Account cleanup for user %s is running elsewhere, skipped", user_id)
            return
        try:
            await run_job(
                CLEANUP_JOB,
                ACCOUNT_CLEANUP_RETRY,
                lambda: runner.run(user_id),
                on_final_failure=lambda error: runner.report_failure(user_id, error),
            )
        finally:
            await context.locks.release(run_lock_key(user_id), token)
            if context.redis is not None:
                await context.redis.delete(
                    pending_key(context.settings.telemetry.service_name, user_id)
                )


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
        pending = pending_key(self.context.settings.telemetry.service_name, user_id)
        try:
            if redis is not None and not await redis.set(
                pending, "1", px=PENDING_KEY_TTL_MS, nx=True
            ):
                return
            try:
                await queue.enqueue(clean_account, {"userId": str(user_id)})
            except BaseException:
                if redis is not None:
                    await redis.delete(pending)
                raise
        except Exception as error:
            log_account_cleanup_failure(user_id, error)
