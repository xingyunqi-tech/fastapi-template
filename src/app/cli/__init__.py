import asyncio
from typing import Annotated

import typer
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
lazy from taskiq.cli.scheduler.args import SchedulerArgs
lazy from taskiq.cli.scheduler.run import run_scheduler
lazy from taskiq.cli.worker.args import WorkerArgs
lazy from taskiq.cli.worker.run import run_worker

from app.cli.seed import seed as run_seed
from app.config import SchemaOwner, Settings, get_settings
from app.core.permissions.service import ROLE_PERMISSIONS_CACHE_KEY
from app.factory import build_context
from app.infrastructure.logging import configure_logging

cli = typer.Typer(no_args_is_help=True, add_completion=False)
permissions_cli = typer.Typer(no_args_is_help=True)
cli.add_typer(permissions_cli, name="permissions", help="权限")


@cli.command()
def migrate() -> None:
    """执行 Alembic 迁移到最新版本；DB_SCHEMA_OWNER=external 时跳过。"""
    settings = get_settings()
    if settings.database.schema_owner is SchemaOwner.EXTERNAL:
        typer.echo("DB_SCHEMA_OWNER=external，跳过迁移")
        return
    command.upgrade(Config("alembic.ini"), "head")


@cli.command()
def seed() -> None:
    """写入角色、状态、权限、行政区划、配置与初始账号（已存在的不覆盖）。"""

    async def run() -> None:
        context = build_context(get_settings())
        async with context.database.transaction() as session:
            await run_seed(session, context.settings)
        await context.cache.delete(ROLE_PERMISSIONS_CACHE_KEY)
        await context.database.dispose()

    asyncio.run(run())


@permissions_cli.command("clear-cache")
def clear_permission_cache() -> None:
    """清除角色权限缓存。"""

    async def run() -> None:
        context = build_context(get_settings())
        await context.cache.delete(ROLE_PERMISSIONS_CACHE_KEY)
        await context.database.dispose()

    asyncio.run(run())


WORKER_TARGET = "app.infrastructure.queue.worker"
REDIS_PING_TIMEOUT_S = 5.0


async def _ping_redis(settings: Settings) -> None:
    client = Redis.from_url(
        settings.redis.url(),
        socket_connect_timeout=REDIS_PING_TIMEOUT_S,
        socket_timeout=REDIS_PING_TIMEOUT_S,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


@cli.command()
def worker(
    workers: Annotated[int, typer.Option(min=1, help="worker 进程数")] = 1,
) -> None:
    """启动队列 worker（Taskiq，需 REDIS_ENABLED=true）。"""
    settings = get_settings()
    if not settings.redis.enabled:
        typer.echo("REDIS_ENABLED=false：任务在 API 进程内执行，无需单独的 worker")
        raise typer.Exit(1)
    configure_logging(settings)
    try:
        asyncio.run(_ping_redis(settings))
    except Exception as error:
        typer.echo(f"Redis 不可用：{error}", err=True)
        raise typer.Exit(1) from None
    status = run_worker(
        WorkerArgs(
            broker=f"{WORKER_TARGET}:broker",
            modules=[],
            workers=workers,
            configure_logging=False,
        )
    )
    raise typer.Exit(status or 0)


@cli.command()
def scheduler() -> None:
    """启动定时任务调度进程（Taskiq scheduler），按 cron 投递清理任务。"""
    asyncio.run(
        run_scheduler(
            SchedulerArgs(
                scheduler=f"{WORKER_TARGET}:scheduler", modules=[], configure_logging=False
            )
        )
    )


def main() -> None:
    cli()
