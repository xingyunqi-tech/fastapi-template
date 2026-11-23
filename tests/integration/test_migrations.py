import asyncio
import os
import uuid
from argparse import Namespace
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.infrastructure.database.base import Branch
from app.infrastructure.database.branches import schema_diff
from app.infrastructure.database.session import Database
from app.modules.files.models import FileModel
from tests.integration.conftest import ROOT, PgServer, _execute

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

PLATFORM_TABLES = {"files", "audit_logs"}

type Alembic = Callable[..., Awaitable[None]]


@pytest.fixture
async def empty_db(pg_server: PgServer) -> str:
    name = f"migrations_{uuid.uuid4().hex[:12]}"
    await _execute(pg_server.dsn("postgres"), f'CREATE DATABASE "{name}"')
    return name


@pytest.fixture
def alembic(pg_server: PgServer, empty_db: str, monkeypatch: pytest.MonkeyPatch) -> Alembic:
    """在线程中执行 Alembic 命令（env.py 自行运行事件循环）。"""
    monkeypatch.setenv("DATABASE_URL", pg_server.url(empty_db))

    async def run(
        name: str, *args: str, schema: str = "public", branches: str | None = None
    ) -> None:
        monkeypatch.setenv("DATABASE_SCHEMA", schema)
        config = Config(os.path.join(ROOT, "alembic.ini"))
        if branches is not None:
            config.cmd_opts = Namespace(x=[f"branches={branches}"])
        await asyncio.to_thread(getattr(command, name), config, *args)

    return run


async def _engine(pg_server: PgServer, database: str, schema: str) -> AsyncEngine:
    settings = pg_server.settings(database, db_schema=schema)
    return create_async_engine(settings.sqlalchemy_url(), connect_args=settings.connect_args())


async def _tables(engine: AsyncEngine, schema: str) -> set[str]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"),
            {"schema": schema},
        )
    return {row[0] for row in rows} - {"alembic_version"}


async def _diff(engine: AsyncEngine, branches: frozenset[Branch]) -> list[object]:
    async with engine.connect() as connection:
        return await connection.run_sync(lambda sync: schema_diff(sync, branches))


async def test_platform_branch_upgrade_check_downgrade_upgrade(
    alembic: Alembic, pg_server: PgServer, empty_db: str
) -> None:
    engine = await _engine(pg_server, empty_db, "public")
    platform = frozenset({Branch.PLATFORM})
    try:
        await alembic("upgrade", "platform@head")
        assert await _tables(engine, "public") == PLATFORM_TABLES
        assert await _diff(engine, platform) == []

        await alembic("downgrade", "platform@base")
        assert await _tables(engine, "public") == set()

        await alembic("upgrade", "platform@head")
        assert await _diff(engine, platform) == []
    finally:
        await engine.dispose()


async def test_all_branches_match_models(
    alembic: Alembic, pg_server: PgServer, empty_db: str
) -> None:
    engine = await _engine(pg_server, empty_db, "public")
    try:
        await alembic("upgrade", "heads")
        assert await _diff(engine, frozenset(Branch)) == []
        await alembic("check")
        await alembic("check", branches="platform,modules")
        await alembic("downgrade", "base")
        assert await _tables(engine, "public") == set()
    finally:
        await engine.dispose()


def _file(name: str) -> FileModel:
    return FileModel(
        owner_id="u",
        purpose="avatar",
        storage_key=name,
        name=name,
        content_type="image/png",
        size=1,
    )


async def test_two_schemas_coexist_in_one_database(
    alembic: Alembic, pg_server: PgServer, empty_db: str
) -> None:
    await alembic("upgrade", "heads", schema="svc_a")
    await alembic("upgrade", "platform@head", schema="svc_b")
    first = Database.from_settings(pg_server.settings(empty_db, db_schema="svc_a"))
    second = Database.from_settings(pg_server.settings(empty_db, db_schema="svc_b"))
    try:
        async with first.transaction() as session:
            created = _file("a.png")
            session.add(created)
        assert created.id.version == 7
        async with second.transaction() as session:
            session.add(_file("b.png"))

        async with first.transaction() as session:
            assert list(await session.scalars(select(FileModel.name))) == ["a.png"]
        async with second.transaction() as session:
            assert list(await session.scalars(select(FileModel.name))) == ["b.png"]
        assert first.engine is not None
        assert second.engine is not None
        assert "users" in await _tables(first.engine, "svc_a")
        assert await _tables(second.engine, "svc_b") == PLATFORM_TABLES
        assert await _tables(first.engine, "public") == set()
        async with first.engine.connect() as connection:
            versions = await connection.scalars(
                text(
                    "SELECT table_schema FROM information_schema.tables "
                    "WHERE table_name = 'alembic_version'"
                )
            )
            assert set(versions) == {"svc_a", "svc_b"}
    finally:
        await first.dispose()
        await second.dispose()


async def test_primary_keys_are_uuid_v7_and_timestamps_have_time_zone(
    alembic: Alembic, pg_server: PgServer, empty_db: str
) -> None:
    await alembic("upgrade", "heads")
    engine = await _engine(pg_server, empty_db, "public")
    try:
        async with engine.begin() as connection:
            server_id = await connection.scalar(
                text(
                    "INSERT INTO files (owner_id, purpose, storage_key, name, content_type, size) "
                    "VALUES ('u', 'avatar', 'k', 'x', 'image/png', 1) RETURNING id"
                )
            )
            types = set(
                await connection.scalars(
                    text(
                        "SELECT DISTINCT data_type FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND column_name LIKE '%\\_at'"
                    )
                )
            )
        assert server_id.version == 7
        assert types == {"timestamp with time zone"}
    finally:
        await engine.dispose()
