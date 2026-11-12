import asyncio
import hashlib
from typing import TYPE_CHECKING

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from app.common.security import Jwt
from app.infrastructure.database.session import Database
from app.infrastructure.metrics import throttler_storage_fallback_total
from app.infrastructure.throttler import ThrottlerStorage, redis_key
from tests.conftest import TEST_ENV
from tests.integration.conftest import _build_app, client_for

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncConnection

CLIENT_IP = "203.0.113.7"
RATE_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")
S3_ENV = {
    "ACCESS_KEY_ID": "key",
    "SECRET_ACCESS_KEY": "secret",
    "AWS_DEFAULT_S3_BUCKET": "bucket",
    "AWS_S3_REGION": "us-east-1",
}
TOO_MANY = {"code": 429, "message": "Too many requests, please try again later", "data": None}

type AppFactory = Callable[..., FastAPI]


def nest_key(handler: str, tracker: str) -> str:
    return hashlib.sha256(f"{handler}-default-{tracker}".encode()).hexdigest()


def user_token(user_id: int) -> dict[str, str]:
    token = Jwt.sign({"id": user_id, "sessionId": 999_999}, TEST_ENV["AUTH_JWT_SECRET"], "15m")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_app(
    connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., FastAPI]:
    def factory(redis: Redis | None = None, **env: str) -> FastAPI:
        monkeypatch.setenv("THROTTLE_ENABLED", "true")
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return _build_app(Database.bound_to(connection), redis=redis)

    return factory


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


class TestHeadersAndLimit:
    async def test_default_rule_sets_headers_then_blocks(self, make_app: AppFactory) -> None:
        async with client_for(make_app(THROTTLE_LIMIT="3", THROTTLE_TTL="60")) as client:
            responses = [await client.get("/") for _ in range(4)]

        for response, remaining in zip(responses[:3], ["2", "1", "0"], strict=True):
            assert response.status_code == 200
            assert response.headers["x-ratelimit-limit"] == "3"
            assert response.headers["x-ratelimit-remaining"] == remaining
            assert response.headers["x-ratelimit-reset"].isdigit()
        blocked = responses[3]
        assert blocked.status_code == 429
        assert blocked.json() == TOO_MANY
        assert int(blocked.headers["retry-after"]) >= 1
        assert not any(name in blocked.headers for name in RATE_HEADERS)

    async def test_zh_message(self, make_app: AppFactory) -> None:
        async with client_for(make_app(THROTTLE_LIMIT="1")) as client:
            await client.get("/")
            response = await client.get("/", headers={"Accept-Language": "zh"})

        assert response.status_code == 429
        assert response.json()["data"] is None
        assert response.json()["message"] != TOO_MANY["message"]

    async def test_route_rule_overrides_default_and_precedes_validation(
        self, make_app: AppFactory
    ) -> None:
        async with client_for(make_app(THROTTLE_LIMIT="100")) as client:
            responses = [await client.post("/api/v1/auth/email/login", json={}) for _ in range(6)]

        assert [r.status_code for r in responses] == [422] * 5 + [429]
        assert responses[0].headers["x-ratelimit-limit"] == "5"
        assert responses[4].headers["x-ratelimit-remaining"] == "0"

    async def test_routes_are_counted_separately(self, make_app: AppFactory) -> None:
        async with client_for(make_app(THROTTLE_LIMIT="1")) as client:
            first = await client.get("/")
            other = await client.get("/api/health")

        assert first.status_code == 200
        assert other.status_code == 200

    async def test_disabled_sends_no_headers(self, make_app: AppFactory) -> None:
        async with client_for(make_app(THROTTLE_ENABLED="false", THROTTLE_LIMIT="1")) as client:
            responses = [await client.get("/") for _ in range(3)]

        assert [r.status_code for r in responses] == [200, 200, 200]
        assert not any(name in responses[0].headers for name in RATE_HEADERS)

    async def test_unmatched_route_is_not_throttled(self, make_app: AppFactory) -> None:
        async with client_for(make_app(THROTTLE_LIMIT="1")) as client:
            responses = [await client.get("/api/v1/nope") for _ in range(3)]

        assert [r.status_code for r in responses] == [404, 404, 404]
        assert "x-ratelimit-limit" not in responses[0].headers


class TestTracker:
    async def test_jwt_routes_count_per_user(self, make_app: AppFactory) -> None:
        async with client_for(make_app(THROTTLE_LIMIT="2")) as client:
            first = [await client.get("/api/v1/auth/me", headers=user_token(1)) for _ in range(3)]
            second = await client.get("/api/v1/auth/me", headers=user_token(2))

        assert [r.status_code for r in first] == [401, 401, 429]
        assert first[0].headers["x-ratelimit-limit"] == "2"
        assert second.status_code == 401
        assert second.headers["x-ratelimit-remaining"] == "1"

    async def test_jwt_route_without_valid_token_counts_by_ip(self, make_app: AppFactory) -> None:
        async with client_for(make_app(THROTTLE_LIMIT="2")) as client:
            invalid = {"Authorization": "Bearer nope"}
            statuses = [
                (await client.get("/api/v1/auth/me", headers=headers)).status_code
                for headers in (invalid, {}, invalid)
            ]

        assert statuses == [401, 401, 429]

    async def test_public_route_ignores_token(self, make_app: AppFactory) -> None:
        async with client_for(make_app(THROTTLE_LIMIT="1")) as client:
            first = await client.get("/", headers=user_token(1))
            second = await client.get("/", headers=user_token(2))

        assert (first.status_code, second.status_code) == (200, 429)

    async def test_forwarded_for_with_trust_proxy(self, make_app: AppFactory) -> None:
        async with client_for(make_app(THROTTLE_LIMIT="1", TRUST_PROXY="1")) as client:
            first = await client.get("/", headers={"X-Forwarded-For": "198.51.100.1"})
            second = await client.get("/", headers={"X-Forwarded-For": "198.51.100.2"})
            repeated = await client.get("/", headers={"X-Forwarded-For": "198.51.100.1"})

        assert [first.status_code, second.status_code, repeated.status_code] == [200, 200, 429]

    async def test_forwarded_for_ignored_without_trust_proxy(self, make_app: AppFactory) -> None:
        async with client_for(make_app(THROTTLE_LIMIT="1")) as client:
            first = await client.get("/", headers={"X-Forwarded-For": "198.51.100.1"})
            second = await client.get("/", headers={"X-Forwarded-For": "198.51.100.2"})

        assert [first.status_code, second.status_code] == [200, 429]


class TestRedisStorage:
    async def test_uses_nestjs_key_in_redis(
        self, make_app: AppFactory, redis_client: Redis
    ) -> None:
        app = make_app(redis_client, THROTTLE_LIMIT="2")
        async with client_for(app) as client:
            responses = [await client.get("/") for _ in range(3)]

        key = redis_key(nest_key("HomeController-appInfo", f"ip:{CLIENT_IP}"))
        assert key.endswith(":default:gcra")
        assert [r.status_code for r in responses] == [200, 200, 429]
        assert responses[1].headers["x-ratelimit-remaining"] == "0"
        assert await redis_client.exists(key) == 1
        assert 0 < await redis_client.pttl(key) <= 60_000
        assert app.state.throttler.memory._tat == {}

    async def test_user_key_in_redis(self, make_app: AppFactory, redis_client: Redis) -> None:
        async with client_for(make_app(redis_client)) as client:
            await client.put("/api/v1/auth/me", headers=user_token(42), json={})

        key = redis_key(nest_key("AuthController-update", "user:42"))
        assert await redis_client.exists(key) == 1

    async def test_coexists_with_nestjs_counter_keys(
        self, make_app: AppFactory, redis_client: Redis
    ) -> None:
        digest = nest_key("HomeController-appInfo", f"ip:{CLIENT_IP}")
        counter, block = f"throttle:{digest}:default", f"throttle:{digest}:default:block"
        await redis_client.set(counter, "7", px=60_000)
        await redis_client.set(block, "1", px=60_000)
        async with client_for(make_app(redis_client, THROTTLE_LIMIT="2")) as client:
            responses = [await client.get("/") for _ in range(3)]

        assert [r.status_code for r in responses] == [200, 200, 429]
        assert await redis_client.get(counter) == b"7"
        assert await redis_client.get(block) == b"1"
        assert await redis_client.incr(counter) == 8
        assert await redis_client.exists(redis_key(digest)) == 1

    @pytest.mark.parametrize(
        ("driver", "method", "path", "handler"),
        [
            ("local", "GET", "/api/v1/files/a.png", "FilesLocalController-download"),
            (
                "local",
                "GET",
                "/api/v1/files/private/00000000-0000-0000-0000-000000000000",
                "FilesController-readPrivate",
            ),
            ("local", "POST", "/api/v1/files/upload", "FilesLocalController-uploadFile"),
            ("s3", "POST", "/api/v1/files/upload", "FilesS3Controller-uploadFile"),
            (
                "s3-presigned",
                "POST",
                "/api/v1/files/upload",
                "FilesS3PresignedController-uploadFile",
            ),
        ],
    )
    async def test_files_routes_use_nestjs_handler_keys(
        self,
        make_app: AppFactory,
        redis_client: Redis,
        driver: str,
        method: str,
        path: str,
        handler: str,
    ) -> None:
        app = make_app(redis_client, FILE_DRIVER=driver, **S3_ENV)
        async with client_for(app) as client:
            response = await client.request(method, path)

        assert "x-ratelimit-limit" in response.headers
        key = redis_key(nest_key(handler, f"ip:{CLIENT_IP}"))
        assert await redis_client.exists(key) == 1

    async def test_shared_between_instances(
        self, make_app: AppFactory, redis_client: Redis
    ) -> None:
        async with client_for(make_app(redis_client, THROTTLE_LIMIT="1")) as client:
            first = await client.get("/")
        async with client_for(make_app(redis_client, THROTTLE_LIMIT="1")) as client:
            second = await client.get("/")

        assert (first.status_code, second.status_code) == (200, 429)

    async def test_falls_back_to_memory_when_redis_fails(
        self, make_app: AppFactory, connection: AsyncConnection
    ) -> None:
        broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
        app = make_app(broken, THROTTLE_LIMIT="2")
        before = throttler_storage_fallback_total._value.get()
        try:
            async with client_for(app) as client:
                responses = [await client.get("/") for _ in range(3)]
        finally:
            await broken.aclose()

        storage: ThrottlerStorage = app.state.throttler
        assert [r.status_code for r in responses] == [200, 200, 429]
        assert responses[0].headers["x-ratelimit-limit"] == "2"
        assert storage.degraded is True
        assert throttler_storage_fallback_total._value.get() - before == 3
        await asyncio.gather(*storage._pending)
        alerts = await connection.execute(
            text('SELECT type, level FROM admin_alert WHERE "dedupKey" = :key'),
            {"key": "throttler-storage-fallback"},
        )
        assert alerts.all() == [("throttler", "CRITICAL")]

    async def test_recovers_from_fallback(self, make_app: AppFactory, redis_client: Redis) -> None:
        app = make_app(redis_client, THROTTLE_LIMIT="5")
        storage: ThrottlerStorage = app.state.throttler
        storage.degraded = True
        async with client_for(app) as client:
            response = await client.get("/")

        assert response.status_code == 200
        assert storage.degraded is False
