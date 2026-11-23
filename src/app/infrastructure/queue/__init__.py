"""Taskiq 队列：配置 Redis 时经 Stream `{service}:tasks` 交给 worker，否则在本进程内执行。"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Annotated, Any, Final
lazy from collections.abc import Awaitable, Callable

from redis.exceptions import RedisError
from taskiq import AsyncBroker, Context, InMemoryBroker, TaskiqDepends
from taskiq.brokers.shared_broker import AsyncSharedBroker
from taskiq_redis import RedisStreamBroker
lazy from taskiq.decor import AsyncTaskiqDecoratedTask

from app.infrastructure.redis import key as redis_key
from app.infrastructure.telemetry import record_queue_job
lazy from app.common.context import AppContext
lazy from app.config import Settings

logger = logging.getLogger("app.queue")

STARTUP_TIMEOUT_S: Final = 5.0

tasks = AsyncSharedBroker()
"""所有任务登记在此，投递时由 TaskQueue 指定实际 broker。"""


def stream_name(service: str) -> str:
    return redis_key(service, "tasks")


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
    """按重试策略执行任务体，最后一次仍失败时调用 on_final_failure 并抛出；`queue` 为指标标签。"""
    started = time.perf_counter()
    for attempt in range(1, policy.attempts + 1):
        try:
            result = await job()
        except Exception as error:
            if attempt >= policy.attempts or isinstance(error, UnrecoverableError):
                record_queue_job(queue, "failed", time.perf_counter() - started)
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
            record_queue_job(queue, "completed", time.perf_counter() - started)
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
            return cls(InMemoryBroker(propagate_exceptions=False), distributed=False)
        service = settings.telemetry.service_name
        broker = RedisStreamBroker(
            settings.redis.url(),
            queue_name=stream_name(service),
            consumer_group_name=service,
            consumer_id="0",
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
