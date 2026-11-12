"""Taskiq 队列：启用 Redis 时经 Redis Stream 交给 worker，未启用时本进程内执行（仅开发 / 测试）。"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Annotated, Any, Final
lazy from collections.abc import Awaitable, Callable

from redis.exceptions import RedisError
from taskiq import AsyncBroker, Context, InMemoryBroker, TaskiqDepends
from taskiq.brokers.shared_broker import AsyncSharedBroker
from taskiq_redis import RedisStreamBroker
lazy from taskiq.decor import AsyncTaskiqDecoratedTask

from app.infrastructure.metrics import record_queue_job
lazy from app.common.context import AppContext
lazy from app.config import Settings

logger = logging.getLogger("app.queue")

STREAM_PREFIX: Final = "taskiq:"
MAIL_QUEUE: Final = "mail"
NOTIFICATION_QUEUE: Final = "notification"
ACCOUNT_CLEANUP_QUEUE: Final = "account-cleanup"
SCHEDULER_QUEUE: Final = "scheduler"
QUEUES: Final = (MAIL_QUEUE, NOTIFICATION_QUEUE, ACCOUNT_CLEANUP_QUEUE, SCHEDULER_QUEUE)
STARTUP_TIMEOUT_S: Final = 5.0

tasks = AsyncSharedBroker()
"""所有任务登记在此，投递时由 TaskQueue 指定实际 broker。"""


def stream_name(queue: str) -> str:
    return STREAM_PREFIX + queue


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """BullMQ `attempts` + `backoff: exponential`：第 n 次失败后等待 delay * 2^(n-1)。"""

    attempts: int
    delay_ms: int

    def backoff_seconds(self, attempts_made: int) -> float:
        return float(round(2 ** (attempts_made - 1) * self.delay_ms)) / 1000


class UnrecoverableError(Exception):
    """BullMQ `UnrecoverableError`：任务体抛出后不重试，直接按最终失败处理。"""


DEFAULT_RETRY: Final = RetryPolicy(attempts=3, delay_ms=1000)

sleep = asyncio.sleep


async def run_job[T](
    queue: str,
    policy: RetryPolicy,
    job: Callable[[], Awaitable[T]],
    *,
    on_final_failure: Callable[[BaseException], Awaitable[None]] | None = None,
) -> T:
    """按重试策略执行任务体，最后一次仍失败时调用 on_final_failure 并抛出。"""
    for attempt in range(1, policy.attempts + 1):
        try:
            result = await job()
        except Exception as error:
            if attempt >= policy.attempts or isinstance(error, UnrecoverableError):
                record_queue_job(queue, "failed")
                logger.error("Job of queue %s failed after %d attempts: %s", queue, attempt, error)
                if on_final_failure is not None:
                    await on_final_failure(error)
                raise
            delay = policy.backoff_seconds(attempt)
            logger.warning(
                "Job of queue %s failed (attempt %d/%d), retrying in %.1fs: %s",
                queue,
                attempt,
                policy.attempts,
                delay,
                error,
            )
            await sleep(delay)
        else:
            record_queue_job(queue, "completed")
            return result
    raise AssertionError("unreachable")


def app_context(context: Annotated[Context, TaskiqDepends()]) -> AppContext:
    """任务内取得所在进程的 AppContext。"""
    app: AppContext = context.state.app_context
    return app


APP_CONTEXT: Final = TaskiqDepends(app_context)


class TaskQueue:
    def __init__(self, broker: AsyncBroker, *, distributed: bool) -> None:
        self.broker = broker
        self.distributed = distributed

    @classmethod
    def from_settings(cls, settings: Settings) -> TaskQueue:
        if not settings.redis.enabled:
            if settings.app.is_production:
                raise RuntimeError("REDIS_ENABLED must be true in production")
            return cls(InMemoryBroker(propagate_exceptions=False), distributed=False)
        main, *others = (stream_name(queue) for queue in QUEUES)
        broker = RedisStreamBroker(
            settings.redis.url(),
            queue_name=main,
            consumer_group_name="taskiq",
            consumer_id="0",
            additional_streams=dict.fromkeys(others, ">"),
        )
        return cls(broker, distributed=True)

    def bind(self, context: AppContext) -> None:
        self.broker.state.app_context = context

    async def enqueue(
        self, task: AsyncTaskiqDecoratedTask[Any, Any], *args: Any, **kwargs: Any
    ) -> None:
        await task.kicker().with_broker(self.broker).kiq(*args, **kwargs)

    async def startup(self) -> None:
        """API 进程启动时声明消费组；Redis 不可达只记日志，不阻塞启动。"""
        if not self.distributed:
            return
        try:
            async with asyncio.timeout(STARTUP_TIMEOUT_S):
                await self.broker.startup()
        except (RedisError, OSError, TimeoutError) as error:
            logger.error("Redis is unavailable, task queue startup skipped: %s", error)

    async def shutdown(self) -> None:
        if isinstance(self.broker, InMemoryBroker):
            await self.broker.wait_all()
            return
        await self.broker.shutdown()
