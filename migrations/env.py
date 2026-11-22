"""Alembic 环境：迁移写入 `DATABASE_SCHEMA`（不存在时先创建），`-x branches=` 限定比较的分支。"""

import asyncio
from typing import Any

from alembic import context
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import DatabaseSettings, SettingsError
from app.infrastructure.database.branches import parse_branches, table_filter
from app.infrastructure.database.models import Base

try:
    settings = DatabaseSettings()
except ValidationError as exc:
    raise SettingsError(
        [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
    ) from None
branches = parse_branches(context.get_x_argument(as_dictionary=True).get("branches"))
schema = settings.db_schema


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=Base.metadata,
        version_table_schema=schema,
        include_object=table_filter(branches),
        compare_type=True,
        compare_server_default=True,
        **kwargs,
    )


def _run(connection: Connection) -> None:
    exists = connection.scalar(
        text("SELECT 1 FROM pg_namespace WHERE nspname = :schema"), {"schema": schema}
    )
    if not exists:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    connection.commit()
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(
        settings.sqlalchemy_url(), connect_args=settings.connect_args(), poolclass=NullPool
    )
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    _configure(
        url=settings.sqlalchemy_url().render_as_string(hide_password=False), literal_binds=True
    )
    with context.begin_transaction():
        context.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        context.execute(f'SET search_path TO "{schema}"')
        context.run_migrations()
else:
    asyncio.run(_run_online())
