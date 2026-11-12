"""testcontainers：会话级 Postgres / Redis，Alembic 迁移与种子写入模板库，每个 worker 克隆一份。"""

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncpg
import httpx as httpx_lib
import httpx2 as httpx
import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer
lazy from fastapi import FastAPI

from app.cli.seed import seed
from app.common.context import AppContext
from app.config import Settings, get_settings
from app.factory import create_app
from app.infrastructure.cache import MemoryCache, RedisCache
from app.infrastructure.database.session import Database
from app.integrations.mail import LoggingMailer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

POSTGRES_IMAGE = "postgres:18.6-alpine"
REDIS_IMAGE = "redis:8.10.3-alpine"
TEMPLATE_DB = "app_template"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DESKTOP_SOCKET = os.path.expanduser("~/.docker/run/docker.sock")
if "DOCKER_HOST" not in os.environ and os.path.exists(_DESKTOP_SOCKET):
    os.environ["DOCKER_HOST"] = f"unix://{_DESKTOP_SOCKET}"
    os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")


@dataclass(frozen=True, slots=True)
class PgServer:
    host: str
    port: int
    user: str
    password: str

    def dsn(self, database: str) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{database}"

    def url(self, database: str) -> str:
        return self.dsn(database).replace("postgresql://", "postgresql+asyncpg://", 1)


async def _execute(dsn: str, statement: str) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(statement)
    finally:
        await connection.close()


async def clone_database_async(server: PgServer, name: str) -> str:
    await _execute(server.dsn("postgres"), f'CREATE DATABASE "{name}" TEMPLATE {TEMPLATE_DB}')
    return name


def clone_database(server: PgServer, name: str) -> str:
    return asyncio.run(clone_database_async(server, name))


@pytest.fixture(scope="session")
def pg_server() -> Iterator[PgServer]:
    with PostgresContainer(
        POSTGRES_IMAGE, username="root", password="secret", dbname="postgres"
    ) as container:
        yield PgServer(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
            user="root",
            password="secret",
        )


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer(REDIS_IMAGE) as container:
        yield f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"


@pytest.fixture(scope="session")
def template_db(pg_server: PgServer) -> str:
    asyncio.run(_execute(pg_server.dsn("postgres"), f"CREATE DATABASE {TEMPLATE_DB}"))
    os.environ["DATABASE_URL"] = pg_server.url(TEMPLATE_DB)
    get_settings.cache_clear()
    command.upgrade(Config(os.path.join(ROOT, "alembic.ini")), "head")

    async def run_seed() -> None:
        settings = Settings.load()
        database = Database.from_settings(settings.database)
        async with database.transaction() as session:
            await seed(session, settings)
        await database.dispose()

    asyncio.run(run_seed())
    return TEMPLATE_DB


@pytest.fixture(scope="session")
def worker_db(pg_server: PgServer, template_db: str, worker_id: str) -> str:
    return clone_database(pg_server, f"test_{worker_id}_{uuid.uuid4().hex[:8]}")


@pytest.fixture(scope="session")
async def engine(pg_server: PgServer, worker_db: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pg_server.url(worker_db))
    yield engine
    await engine.dispose()


@pytest.fixture
async def connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


def _build_app(database: Database, *, redis: Redis | None = None) -> FastAPI:
    get_settings.cache_clear()
    settings = Settings.load()
    app = create_app(settings)
    app.state.context = AppContext(
        settings=settings,
        database=database,
        cache=RedisCache(redis) if redis is not None else MemoryCache(),
        passwords=app.state.context.passwords,
        mailer=LoggingMailer(),
        redis=redis,
        http_transport=httpx_lib.MockTransport(_reject_external_request),
    )
    return app


def _reject_external_request(request: httpx_lib.Request) -> httpx_lib.Response:
    raise AssertionError(f"测试不允许访问外部网络：{request.method} {request.url}")


@pytest.fixture
def app(connection: AsyncConnection) -> FastAPI:
    """回滚模式：每个用例结束后撤销全部写入。"""
    return _build_app(Database.bound_to(connection))


@pytest.fixture
async def committed_app(
    pg_server: PgServer, template_db: str, redis_url: str
) -> AsyncIterator[FastAPI]:
    """真实提交模式：从模板克隆独立库，并使用 Redis 缓存。"""
    name = await clone_database_async(pg_server, f"committed_{uuid.uuid4().hex[:12]}")
    database = Database.from_settings(
        Settings.load().database.model_copy(update={"url": pg_server.url(name)})
    )
    redis = Redis.from_url(redis_url)
    yield _build_app(database, redis=redis)
    await database.dispose()
    await redis.aclose()


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.7", 50000)),
        base_url="http://test",
        trust_env=False,
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(app) as http:
        yield http
