"""worker 与 scheduler 进程加载的 broker / scheduler：导入全部任务并在进程内构建 AppContext。"""

from taskiq import TaskiqEvents, TaskiqState

from app.config import get_settings
from app.factory import build_context
from app.identity.account_deletion import cleanup as _account_cleanup  # noqa: F401
from app.infrastructure.logging import configure_logging
from app.infrastructure.queue import mail as _mail  # noqa: F401
from app.infrastructure.scheduler import create_scheduler
from app.infrastructure.telemetry import setup_sentry, start_metrics_server

settings = get_settings()
configure_logging(settings)
setup_sentry(settings)
start_metrics_server(settings)
context = build_context(settings)
if context.queue is None:
    raise RuntimeError("task queue is not configured")
queue = context.queue
broker = queue.broker
scheduler = create_scheduler(queue)


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _dispose(state: TaskiqState) -> None:
    await context.database.dispose()
    if context.redis is not None:
        await context.redis.aclose()
