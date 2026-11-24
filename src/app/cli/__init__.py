import asyncio
from typing import Annotated, Final
lazy from uuid import UUID

import typer
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
lazy from taskiq.cli.scheduler.args import SchedulerArgs
lazy from taskiq.cli.scheduler.run import run_scheduler
lazy from taskiq.cli.worker.args import WorkerArgs
lazy from taskiq.cli.worker.run import run_worker

from app.cli.generate import generate_cli
from app.cli.openapi import openapi_cli
from app.cli.seed import seed as run_seed
from app.config import Settings, get_settings
from app.factory import build_context
from app.identity.account_deletion.cleanup import AccountCleanupRunner
from app.infrastructure.database.branches import migration_problems, migration_targets
from app.infrastructure.logging import configure_logging
from app.security.keys import generate_key_pair

CLI_DATABASE_POOL_SIZE: Final = 1

cli = typer.Typer(no_args_is_help=True, add_completion=False)
keys_cli = typer.Typer(no_args_is_help=True)
users_cli = typer.Typer(no_args_is_help=True)
cli.add_typer(keys_cli, name="keys", help="签名密钥")
cli.add_typer(users_cli, name="users", help="用户")
cli.add_typer(generate_cli, name="generate", help="代码生成")
cli.add_typer(openapi_cli, name="openapi", help="OpenAPI 文档")


@cli.command()
def migrate(
    check: Annotated[
        bool, typer.Option("--check", help="只检查是否已是最新版本且与模型一致，否则退出码 1")
    ] = False,
) -> None:
    """在 `DATABASE_SCHEMA` 中升级本角色的分支：签发方全部分支，验证方 platform + modules。"""
    settings = get_settings()
    if not settings.database.enabled:
        typer.echo("未设置 DATABASE_URL，无需迁移")
        return
    config = Config("alembic.ini")
    if not check:
        for target in migration_targets(settings):
            command.upgrade(config, target)
        return
    problems = asyncio.run(_migration_problems(settings, config))
    for problem in problems:
        typer.echo(problem, err=True)
    if problems:
        raise typer.Exit(1)
    typer.echo("数据库迁移已是最新，与模型一致")


async def _migration_problems(settings: Settings, config: Config) -> list[str]:
    engine = create_async_engine(
        settings.database.sqlalchemy_url(),
        connect_args=settings.database.connect_args(),
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync: migration_problems(sync, config, settings)
            )
    finally:
        await engine.dispose()


def _require_issuer(settings: Settings) -> None:
    if not settings.auth.is_issuer:
        typer.echo("只适用于签发方（设置 AUTH_SIGNING_KEY 的服务）", err=True)
        raise typer.Exit(1)


@cli.command()
def seed() -> None:
    """无 active admin 时创建管理员账号（仅签发方）。"""
    _require_issuer(get_settings())

    async def run() -> bool:
        context = build_context(get_settings(), pool_size=CLI_DATABASE_POOL_SIZE)
        try:
            async with context.db.transaction() as session:
                return await run_seed(session, context.settings)
        finally:
            await context.db.dispose()

    typer.echo(
        "已创建管理员账号" if asyncio.run(run()) else "已有管理员账号或未提供管理员凭据，未创建"
    )


@keys_cli.command("generate")
def generate_keys() -> None:
    """生成 Ed25519 密钥对：私钥用于 `AUTH_SIGNING_KEY`，公钥用于其他服务的 `AUTH_VERIFY_KEYS`。"""
    pair = generate_key_pair()
    typer.echo(pair.private_pem + pair.public_pem, nl=False)


@users_cli.command("cleanup")
def cleanup_user(user_id: Annotated[UUID, typer.Argument(help="已注销用户的 id")]) -> None:
    """重新执行该用户的注销数据清理（仅签发方）。"""
    _require_issuer(get_settings())

    async def run() -> None:
        context = build_context(get_settings(), pool_size=CLI_DATABASE_POOL_SIZE)
        try:
            await AccountCleanupRunner.from_context(context).run(user_id)
        finally:
            await context.db.dispose()

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
    """启动队列 worker（Taskiq，需 REDIS_URL）。"""
    settings = get_settings()
    if not settings.redis.enabled:
        typer.echo("未设置 REDIS_URL：任务在 API 进程内执行，无需单独的 worker")
        raise typer.Exit(1)
    if workers > 1 and settings.telemetry.prometheus_enabled:
        typer.echo("OTEL_METRICS_EXPORTER=prometheus 时 --workers 只能为 1", err=True)
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
