"""定时任务：scheduler 进程按 cron 投递，执行时持有 `lock:scheduler:<name>` 防止多实例并发执行。"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final
lazy from collections.abc import Awaitable, Callable

from sqlalchemy import text
from taskiq import ScheduledTask, ScheduleSource, TaskiqScheduler

from app.infrastructure.account_cleanup import AccountCleanupProducer, AccountCleanupRepository
from app.infrastructure.database.session import affected_rows
from app.infrastructure.queue import APP_CONTEXT, SCHEDULER_QUEUE, stream_name, tasks
lazy from app.common.context import AppContext
lazy from app.infrastructure.queue import TaskQueue
lazy from app.infrastructure.redis import LockService

logger = logging.getLogger("app.scheduler")

LOCK_PREFIX: Final = "scheduler:"
DEFAULT_LOCK_TTL_MS: Final = 10 * 60 * 1000
ACCOUNT_CLEANUP_GRACE: Final = timedelta(minutes=30)
ACCOUNT_CLEANUP_BATCH_SIZE: Final = 200


@dataclass(frozen=True, slots=True)
class ExclusiveRun[T]:
    executed: bool
    result: T | None = None


async def run_exclusive[T](
    locks: LockService, key: str, ttl_ms: int, task: Callable[[], Awaitable[T]]
) -> ExclusiveRun[T]:
    """持锁执行 task；其它实例持有锁时跳过。task 自身须以条件更新保证幂等。"""
    lock_key = LOCK_PREFIX + key
    token = await locks.try_acquire(lock_key, ttl_ms)
    if token is None:
        logger.debug("[%s] Skipped: running on another instance", key)
        return ExclusiveRun(executed=False)
    try:
        return ExclusiveRun(executed=True, result=await task())
    finally:
        await locks.release(lock_key, token)


async def cleanup_old_sessions(context: AppContext) -> int:
    """物理删除软删除超过 7 天的会话。"""
    async with context.database.transaction() as session:
        result = await session.execute(
            text(
                'DELETE FROM session WHERE "deletedAt" IS NOT NULL '
                "AND \"deletedAt\" < NOW() - INTERVAL '7 days'"
            )
        )
    return affected_rows(result)


async def cleanup_read_notifications(context: AppContext) -> int:
    """软删除已读超过 30 天的通知。"""
    async with context.database.transaction() as session:
        result = await session.execute(
            text(
                'UPDATE notification SET "deletedAt" = NOW() '
                'WHERE "isRead" = true AND "readAt" IS NOT NULL '
                "AND \"readAt\" < NOW() - INTERVAL '30 days' "
                'AND "deletedAt" IS NULL'
            )
        )
    return affected_rows(result)


async def enqueue_pending_account_cleanups(context: AppContext) -> int:
    """已注销超过 30 分钟仍未完成清理的用户重新入队。"""
    async with context.database.transaction() as session:
        user_ids = await AccountCleanupRepository(session).find_pending_user_ids(
            datetime.now(UTC) - ACCOUNT_CLEANUP_GRACE, ACCOUNT_CLEANUP_BATCH_SIZE
        )
    producer = AccountCleanupProducer(context)
    for user_id in user_ids:
        await producer.enqueue_cleanup(user_id)
    return len(user_ids)


async def _run_task(
    context: AppContext,
    name: str,
    work: Callable[[AppContext], Awaitable[int]],
    describe: Callable[[int], str],
) -> None:
    logger.info("[%s] Starting", name)
    try:
        run = await run_exclusive(context.locks, name, DEFAULT_LOCK_TTL_MS, lambda: work(context))
    except Exception:
        logger.exception("[%s] Task failed", name)
        return
    if run.executed and run.result is not None:
        logger.info("[%s] %s", name, describe(run.result))


def _schedule(cron: str, cron_offset: str) -> dict[str, Any]:
    return {"schedule": [{"cron": cron, "cron_offset": cron_offset}]}


_QUEUE: Final = {"queue_name": stream_name(SCHEDULER_QUEUE)}


@tasks.task(task_name="scheduler:cleanup-old-sessions", **_QUEUE, **_schedule("0 3 * * *", "UTC"))
async def cleanup_old_sessions_task(context: Annotated[AppContext, APP_CONTEXT]) -> None:
    await _run_task(
        context,
        "cleanup-old-sessions",
        cleanup_old_sessions,
        lambda count: f"Cleanup completed: {count} old sessions permanently deleted",
    )


@tasks.task(
    task_name="scheduler:cleanup-read-notifications", **_QUEUE, **_schedule("0 4 * * *", "UTC")
)
async def cleanup_read_notifications_task(context: Annotated[AppContext, APP_CONTEXT]) -> None:
    await _run_task(
        context,
        "cleanup-read-notifications",
        cleanup_read_notifications,
        lambda count: f"Cleanup completed: {count} old read notifications soft deleted",
    )


@tasks.task(
    task_name="scheduler:account-cleanup-compensation",
    **_QUEUE,
    **_schedule("25 * * * *", "Asia/Shanghai"),
)
async def account_cleanup_compensation_task(
    context: Annotated[AppContext, APP_CONTEXT],
) -> None:
    await _run_task(
        context,
        "account-cleanup-compensation",
        enqueue_pending_account_cleanups,
        lambda count: f"{count} account cleanups enqueued",
    )


class SharedTaskSchedules(ScheduleSource):
    """收集共享任务表中带 `schedule` 标签的任务。"""

    def __init__(self) -> None:
        self.schedules: list[ScheduledTask] = []

    async def startup(self) -> None:
        self.schedules = [
            ScheduledTask(
                task_name=name,
                labels={key: value for key, value in task.labels.items() if key != "schedule"},
                schedule_id=f"{name}:{index}",
                args=[],
                kwargs={},
                cron=schedule["cron"],
                cron_offset=schedule.get("cron_offset"),
            )
            for name, task in tasks.get_all_tasks().items()
            for index, schedule in enumerate(task.labels.get("schedule", []))
        ]

    async def get_schedules(self) -> list[ScheduledTask]:
        return list(self.schedules)


def create_scheduler(queue: TaskQueue) -> TaskiqScheduler:
    return TaskiqScheduler(queue.broker, sources=[SharedTaskSchedules()])
