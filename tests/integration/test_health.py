from typing import TYPE_CHECKING, Any

import httpx
import pytest
from redis.asyncio import Redis

from app.config import Settings
from app.infrastructure.database.session import Database
from app.infrastructure.health import indicators
from tests.integration.conftest import _build_app, client_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncConnection

TOKEN = "monitoring-token-0123456789"
NO_CACHE = "no-cache, no-store, must-revalidate"
UNAVAILABLE = {"code": 503, "message": "SERVICE_UNAVAILABLE", "data": None}
FORBIDDEN = {"code": 403, "message": "Forbidden resource", "data": None}

type AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(
    connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., FastAPI]:
    def factory(
        redis: Redis | None = None, database: Database | None = None, **env: str
    ) -> FastAPI:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return _build_app(database or Database.bound_to(connection), redis=redis)

    return factory


@pytest.fixture(autouse=True)
def generous_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(indicators, "DISK_THRESHOLD_PERCENT", 1.0)
    monkeypatch.setattr(indicators, "HEAP_THRESHOLD_BYTES", 1 << 40)


@pytest.fixture
async def broken_database() -> AsyncIterator[Database]:
    settings = Settings.load().database.model_copy(
        update={"url": "postgresql+asyncpg://root:secret@127.0.0.1:1/none"}
    )
    database = Database.from_settings(settings)
    yield database
    await database.dispose()


def mock_http(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], Any]) -> None:
    real = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestProbes:
    async def test_liveness(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            response = await client.get("/api/v1/health/live")

        assert response.status_code == 200
        assert response.headers["cache-control"] == NO_CACHE
        assert response.json() == {
            "code": 200,
            "message": "success",
            "data": {
                "status": "ok",
                "info": {"memory_heap": {"status": "up"}},
                "error": {},
                "details": {"memory_heap": {"status": "up"}},
            },
        }

    async def test_readiness(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            response = await client.get("/api/v1/health/ready")

        data = response.json()["data"]
        assert response.status_code == 200
        assert data["status"] == "ok"
        assert list(data["info"]["database"]) == ["responseTime", "status"]
        assert isinstance(data["info"]["database"]["responseTime"], int)
        assert data["details"] == data["info"]

    async def test_readiness_fails_with_503(
        self, make_app: AppFactory, broken_database: Database
    ) -> None:
        async with client_for(make_app(database=broken_database)) as client:
            response = await client.get("/api/v1/health/ready")

        assert response.status_code == 503
        assert response.json() == UNAVAILABLE
        assert "cache-control" not in response.headers

    async def test_simple_health_check_has_no_cache_header(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            response = await client.get("/api/health")

        assert response.status_code == 200
        assert "cache-control" not in response.headers


class TestFullCheck:
    async def test_without_redis_and_payment_config(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            response = await client.get("/api/v1/health")

        data = response.json()["data"]
        assert response.status_code == 200
        assert response.headers["cache-control"] == NO_CACHE
        assert list(data["details"]) == [
            "memory_heap",
            "disk",
            "database",
            "database_pool",
            "wechat_api",
            "alipay_api",
        ]
        assert data["details"]["wechat_api"] == {
            "message": "WeChat not configured, skipped",
            "status": "up",
        }
        assert data["details"]["alipay_api"] == {
            "message": "Alipay not configured, skipped",
            "status": "up",
        }
        assert list(data["details"]["database_pool"]) == [
            "activeConnections",
            "maxConnections",
            "utilizationPercent",
            "status",
        ]

    async def test_includes_redis_when_enabled(self, make_app: AppFactory, redis_url: str) -> None:
        redis = Redis.from_url(redis_url)
        try:
            async with client_for(make_app(redis, REDIS_ENABLED="true")) as client:
                response = await client.get("/api/v1/health")
        finally:
            await redis.aclose()

        details = response.json()["data"]["details"]
        assert details["redis"] == {"status": "up"}
        assert list(details).index("redis") == 4

    async def test_redis_down(self, make_app: AppFactory) -> None:
        redis = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
        try:
            async with client_for(make_app(redis, REDIS_ENABLED="true")) as client:
                response = await client.get("/api/v1/health")
        finally:
            await redis.aclose()

        assert response.status_code == 503
        assert response.json() == UNAVAILABLE

    async def test_payment_apis_probed_when_configured(
        self, make_app: AppFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, str(request.url)))
            return httpx.Response(405 if request.method == "HEAD" else 200)

        mock_http(monkeypatch, handler)
        app = make_app(WECHAT_APP_ID="wx", WECHAT_APP_SECRET="secret", ALIPAY_APP_ID="ali")
        async with client_for(app) as client:
            response = await client.get("/api/v1/health")

        details = response.json()["data"]["details"]
        assert response.status_code == 200
        assert details["wechat_api"] == {"responseTime": "OK", "status": "up"}
        assert details["alipay_api"] == {"responseTime": "OK", "status": "up"}
        assert sorted(seen) == [
            ("GET", indicators.WECHAT_API_URL),
            ("HEAD", indicators.ALIPAY_GATEWAY_URL),
        ]

    async def test_payment_api_failure_returns_503(
        self, make_app: AppFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_http(monkeypatch, lambda request: httpx.Response(500))
        async with client_for(make_app(ALIPAY_APP_ID="ali")) as client:
            response = await client.get("/api/v1/health")

        assert response.status_code == 503
        assert response.json() == UNAVAILABLE

    async def test_storage_thresholds(
        self, make_app: AppFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(indicators, "DISK_THRESHOLD_PERCENT", 0.0)
        monkeypatch.setattr(indicators, "HEAP_THRESHOLD_BYTES", 0)

        assert await indicators.disk("disk") == {
            "disk": {"message": "Used disk storage exceeded the set threshold", "status": "down"}
        }
        assert await indicators.memory_heap("memory_heap") == {
            "memory_heap": {"message": "Used heap exceeded the set threshold", "status": "down"}
        }
        async with client_for(make_app()) as client:
            live = await client.get("/api/v1/health/live")

        assert live.status_code == 503
        assert live.json() == UNAVAILABLE


class TestMonitoringToken:
    async def test_token_required_when_configured(self, make_app: AppFactory) -> None:
        async with client_for(make_app(METRICS_TOKEN=TOKEN)) as client:
            missing = await client.get("/api/v1/health")
            wrong = await client.get("/api/v1/health", headers=bearer(TOKEN + "x"))
            scheme = await client.get("/api/v1/health", headers={"Authorization": f"Token {TOKEN}"})
            ok = await client.get("/api/v1/health", headers=bearer(TOKEN))
            lower = await client.get("/api/v1/health", headers={"Authorization": f"bearer {TOKEN}"})

        for response in (missing, wrong, scheme):
            assert response.status_code == 403
            assert response.json() == FORBIDDEN
        assert ok.status_code == 200
        assert lower.status_code == 200

    async def test_probes_do_not_require_token(self, make_app: AppFactory) -> None:
        async with client_for(make_app(METRICS_TOKEN=TOKEN)) as client:
            live = await client.get("/api/v1/health/live")
            ready = await client.get("/api/v1/health/ready")

        assert (live.status_code, ready.status_code) == (200, 200)
