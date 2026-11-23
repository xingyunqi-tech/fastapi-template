from typing import TYPE_CHECKING

import pytest
from redis.asyncio import Redis

from app.config import DatabaseSettings
from app.infrastructure.database.session import Database
from tests.integration.conftest import _build_app, client_for

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncConnection

type AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(connection: AsyncConnection) -> AppFactory:
    def factory(redis: Redis | None = None, database: Database | None = None) -> FastAPI:
        return _build_app(database or Database.bound_to(connection), redis=redis)

    return factory


def unreachable_database() -> Database:
    return Database.from_settings(
        DatabaseSettings.model_validate(
            {"DATABASE_URL": "postgresql://u:p@127.0.0.1:1/db?sslmode=disable"}
        )
    )


class TestProbes:
    async def test_liveness(self, make_app: AppFactory) -> None:
        async with client_for(make_app(database=unreachable_database())) as client:
            response = await client.get("/health/live")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert response.headers["cache-control"] == "no-store"

    async def test_readiness_checks_database_and_redis(
        self, make_app: AppFactory, redis_url: str
    ) -> None:
        redis = Redis.from_url(redis_url)
        async with client_for(make_app(redis)) as client:
            response = await client.get("/health/ready")
        await redis.aclose()

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert response.headers["cache-control"] == "no-store"

    async def test_database_down_is_503(self, make_app: AppFactory) -> None:
        database = unreachable_database()
        async with client_for(make_app(database=database)) as client:
            response = await client.get("/health/ready")
        await database.dispose()

        assert response.status_code == 503
        assert response.json() == {"status": "error"}
        assert response.headers["retry-after"] == "5"

    async def test_redis_down_is_503(self, make_app: AppFactory) -> None:
        redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
        async with client_for(make_app(redis)) as client:
            response = await client.get("/health/ready")

        assert (response.status_code, response.json()) == (503, {"status": "error"})

    @pytest.mark.parametrize("path", ["/api/v1/health/live", "/health", "/api/health/ready"])
    async def test_only_root_paths_exist(self, make_app: AppFactory, path: str) -> None:
        async with client_for(make_app()) as client:
            assert (await client.get(path)).status_code == 404

    def test_probes_are_not_in_openapi(self, make_app: AppFactory) -> None:
        paths = make_app().openapi()["paths"]

        assert not any(path.startswith("/health") for path in paths)
