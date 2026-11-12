import asyncio

import typer
from alembic import command
from alembic.config import Config

from app.cli.seed import seed as run_seed
from app.config import SchemaOwner, get_settings
from app.core.permissions.service import ROLE_PERMISSIONS_CACHE_KEY
from app.factory import build_context

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


def main() -> None:
    cli()
