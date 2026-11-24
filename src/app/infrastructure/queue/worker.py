"""worker 与 scheduler 进程：导入全部任务、构建 AppContext；验证方 worker 另运行事件消费者。"""

import asyncio
import logging

from taskiq import TaskiqEvents, TaskiqState

from app.config import get_settings
from app.factory import build_context
from app.identity.account_deletion import cleanup as _account_cleanup  # noqa: F401
from app.infrastructure.events.consumer import build_consumer
from app.infrastructure.logging import configure_logging
from app.infrastructure.queue import mail as _mail  # noqa: F401
from app.infrastructure.scheduler import create_scheduler
from app.infrastructure.telemetry import setup_sentry, start_metrics_server

logger = logging.getLogger("app.events")
settings = get_settings()
configure_logging(settings)
setup_sentry(settings)
start_metrics_server(settings)
context = build_context(settings)
if context.queue is None:
    raise RuntimeError("task queue is not configured")
queue = context.queue
broker = queue.broker
scheduler = create_scheduler(queue, settings)
_consumers: list[asyncio.Task[None]] = []


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _start_consumer(state: TaskiqState) -> None:
    consumer = build_consumer(context)
    if consumer is not None:
        task = asyncio.create_task(consumer.run())
        task.add_done_callback(_consumer_stopped)
        _consumers.append(task)


def _consumer_stopped(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("Event consumer stopped: %s", task.exception())


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _dispose(state: TaskiqState) -> None:
    for task in _consumers:
        task.cancel()
    await asyncio.gather(*_consumers, return_exceptions=True)
    if context.database is not None:
        await context.database.dispose()
    if context.redis is not None:
        await context.redis.aclose()
