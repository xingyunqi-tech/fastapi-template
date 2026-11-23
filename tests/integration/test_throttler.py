import asyncio
import time
from typing import TYPE_CHECKING
from uuid import UUID

import httpx2 as httpx
import pytest
from redis.asyncio import Redis

from app.infrastructure.database.session import Database
from app.infrastructure.telemetry import REGISTRY
from tests.integration.conftest import _build_app, client_for, error_of
from tests.integration.identity import PASSWORD, USER_EMAIL, bearer, login
from tests.tokens import access_token

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncConnection

CLIENT_IP = "203.0.113.7"
RATE_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")
DEFAULT_KEY = f"rl:api-default:ip:{CLIENT_IP}"
CONTENT = "/api/v1/files/019a8f3c-6b1e-7d2a-8000-000000000001/content"
LOGIN = "/api/v1/auth/email/login"

type AppFactory = Callable[..., FastAPI]


def user_id(n: int) -> UUID:
    return UUID(f"019a8f3c-6b1e-7d2a-8000-{n:012d}")


def user_token(app: FastAPI, n: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token(app, user_id(n))}"}


def client_at(app: FastAPI, ip: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 50000)),
        base_url="http://test",
        trust_env=False,
    )


@pytest.fixture
def make_app(connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch) -> AppFactory:
    def factory(redis: Redis | None = None, **env: str) -> FastAPI:
        monkeypatch.setenv("RATE_LIMIT_DEFAULT", "1/m,burst=2")
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


def fallback_count(rule: str) -> float:
    return sum(
        sample.value
        for metric in REGISTRY.collect()
        for sample in metric.samples
        if sample.name == "ratelimit_fallback_total" and sample.labels.get("rule") == rule
    )


@pytest.fixture
async def blackhole_redis() -> AsyncIterator[Redis]:
    async def swallow(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while await reader.read(1024):
            pass
        writer.close()

    server = await asyncio.start_server(swallow, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = Redis.from_url(f"redis://127.0.0.1:{port}/0")
    yield client
    server.close()
    server.close_clients()
    await server.wait_closed()


def broken_redis() -> Redis:
    return Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)


class TestHeadersAndLimit:
    async def test_default_rule_sets_headers_then_blocks(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            responses = [await client.get(CONTENT) for _ in range(3)]

        assert [r.status_code for r in responses] == [404, 404, 429]
        assert [r.headers["x-ratelimit-limit"] for r in responses[:2]] == ["2", "2"]
        assert [r.headers["x-ratelimit-remaining"] for r in responses[:2]] == ["1", "0"]
        assert [r.headers["x-ratelimit-reset"] for r in responses[:2]] == ["60", "120"]
        assert error_of(responses[2]) == (429, "common.tooManyRequests")
        assert responses[2].headers["retry-after"] == "60"
        assert not any(name in responses[2].headers for name in RATE_HEADERS)

    async def test_message_follows_accept_language(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            for _ in range(2):
                await client.get(CONTENT)
            response = await client.get(CONTENT, headers={"Accept-Language": "en"})

        assert response.json()["message"] == "Too many requests, please try again later"

    async def test_route_rule_replaces_default_and_precedes_validation(
        self, make_app: AppFactory
    ) -> None:
        async with client_for(make_app()) as client:
            responses = [await client.post(LOGIN, json={}) for _ in range(11)]

        assert [r.status_code for r in responses] == [422] * 10 + [429]
        assert responses[0].headers["x-ratelimit-limit"] == "10"

    async def test_probes_are_not_limited(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            responses = [await client.get("/health/live") for _ in range(3)]

        assert [r.status_code for r in responses] == [200] * 3
        assert "x-ratelimit-limit" not in responses[0].headers

    async def test_unmatched_route_is_not_limited(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            responses = [await client.get("/api/v1/nope") for _ in range(3)]

        assert [r.status_code for r in responses] == [404] * 3
        assert "x-ratelimit-limit" not in responses[0].headers


class TestSubject:
    async def test_jwt_routes_count_per_user(self, make_app: AppFactory) -> None:
        app = make_app()
        async with client_for(app) as client:
            for _ in range(2):
                await client.get("/api/v1/files/" + str(user_id(9)), headers=user_token(app, 1))
            blocked = await client.get("/api/v1/me", headers=user_token(app, 1))
            other = await client.get("/api/v1/me", headers=user_token(app, 2))

        assert blocked.status_code == 429
        assert other.status_code != 429

    async def test_invalid_token_counts_by_ip(self, make_app: AppFactory) -> None:
        bad = {"Authorization": "Bearer nope"}
        async with client_for(make_app()) as client:
            statuses = [(await client.get("/api/v1/me", headers=bad)).status_code for _ in range(2)]
            blocked = await client.get(CONTENT)

        assert statuses == [401, 401]
        assert blocked.status_code == 429

    async def test_public_route_ignores_token(self, make_app: AppFactory) -> None:
        app = make_app()
        async with client_for(app) as client:
            await client.get(CONTENT, headers=user_token(app, 1))
            await client.get(CONTENT, headers=user_token(app, 2))
            blocked = await client.get(CONTENT)

        assert blocked.status_code == 429

    async def test_ipv6_clients_share_a_64_prefix(self, make_app: AppFactory) -> None:
        app = make_app()
        for ip in ("2001:db8:1:2::1", "2001:db8:1:2::2"):
            async with client_at(app, ip) as client:
                await client.get(CONTENT)
        async with client_at(app, "2001:db8:1:2:ffff::9") as client:
            blocked = await client.get(CONTENT)
        async with client_at(app, "2001:db8:1:3::1") as client:
            other = await client.get(CONTENT)

        assert (blocked.status_code, other.status_code) == (429, 404)

    @pytest.mark.parametrize(("trusted", "expected"), [("1", 404), ("0", 429)])
    async def test_forwarded_for_follows_trusted_proxies(
        self, make_app: AppFactory, trusted: str, expected: int
    ) -> None:
        async with client_for(make_app(TRUSTED_PROXIES=trusted)) as client:
            for n in range(2):
                await client.get(CONTENT, headers={"X-Forwarded-For": f"198.51.100.{n}"})
            response = await client.get(CONTENT, headers={"X-Forwarded-For": "198.51.100.9"})

        assert response.status_code == expected


@pytest.mark.committed
class TestRedisStore:
    async def test_key_holds_tat_in_milliseconds(
        self, make_app: AppFactory, redis_client: Redis
    ) -> None:
        async with client_for(make_app(redis_client)) as client:
            await client.get(CONTENT)

        stored = await redis_client.get(DEFAULT_KEY)
        assert isinstance(stored, bytes)
        tat = int(stored)
        seconds, micros = await redis_client.time()
        server_ms = int(seconds) * 1000 + int(micros) // 1000
        assert 0 < tat - server_ms <= 60_000
        assert 0 < await redis_client.pttl(DEFAULT_KEY) <= 60_000

    async def test_user_subject_key(self, make_app: AppFactory, redis_client: Redis) -> None:
        app = make_app(redis_client)
        async with client_for(app) as client:
            await client.get("/api/v1/me", headers=user_token(app, 1))

        assert await redis_client.exists(f"rl:api-default:u:{user_id(1)}") == 1

    async def test_shared_between_instances(
        self, make_app: AppFactory, redis_client: Redis
    ) -> None:
        first, second = make_app(redis_client), make_app(redis_client)
        async with client_for(first) as a, client_for(second) as b:
            await a.get(CONTENT)
            await b.get(CONTENT)
            blocked = await a.get(CONTENT)

        assert blocked.status_code == 429

    async def test_failed_logins_count_per_account(
        self, make_app: AppFactory, redis_client: Redis
    ) -> None:
        body = {"email": "john.doe@example.com", "password": "Wrong000"}
        async with client_for(make_app(redis_client)) as client:
            failures = [await client.post(LOGIN, json=body) for _ in range(5)]
            locked = await client.post(LOGIN, json={**body, "password": "Secret00"})

        assert {error_of(r) for r in failures} == {(401, "auth.invalidCredentials")}
        assert error_of(locked) == (429, "common.tooManyRequests")
        assert 0 < int(locked.headers["retry-after"]) <= 720


class TestRedisFailure:
    async def test_shared_rule_fails_closed(self, make_app: AppFactory) -> None:
        async with client_for(make_app(broken_redis())) as client:
            response = await client.post(LOGIN, json={})

        assert error_of(response) == (503, "common.serviceUnavailable")

    async def test_private_rule_fails_open_and_counts(self, make_app: AppFactory) -> None:
        before = fallback_count("api-default")
        async with client_for(make_app(broken_redis())) as client:
            responses = [await client.get(CONTENT) for _ in range(3)]

        assert [r.status_code for r in responses] == [404] * 3
        assert fallback_count("api-default") == before + 3

    async def test_unresponsive_redis_times_out(
        self, make_app: AppFactory, blackhole_redis: Redis
    ) -> None:
        started = time.monotonic()
        async with client_for(make_app(blackhole_redis)) as client:
            shared = await client.post(LOGIN, json={})
            private = await client.get(CONTENT)

        assert error_of(shared) == (503, "common.serviceUnavailable")
        assert private.status_code == 404
        assert time.monotonic() - started < 5


def forwarded(n: int) -> dict[str, str]:
    return {"X-Forwarded-For": f"198.51.100.{n}"}


class TestSharedRules:
    async def test_register_counts_per_ip(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            responses = [
                await client.post(
                    "/api/v1/auth/email/register",
                    json={"email": f"r{n}@example.com", "password": PASSWORD},
                )
                for n in range(4)
            ]

        assert [r.status_code for r in responses] == [201, 201, 201, 429]

    async def test_forgot_password_counts_per_account_and_ip(self, make_app: AppFactory) -> None:
        forgot = "/api/v1/auth/password/forgot"
        async with client_for(make_app(TRUSTED_PROXIES="1")) as client:
            same = [
                await client.post(forgot, json={"email": USER_EMAIL}, headers=forwarded(n))
                for n in range(3)
            ]
            other = [
                await client.post(forgot, json={"email": f"x{n}@example.com"}, headers=forwarded(9))
                for n in range(4)
            ]

        assert [r.status_code for r in same] == [202, 202, 429]
        assert [r.status_code for r in other] == [202, 202, 202, 429]

    async def test_sms_send_counts_per_ip(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            responses = [
                await client.post(
                    "/api/v1/auth/sms/send",
                    json={"phone": f"+86138000000{n:02d}", "scene": "login"},
                )
                for n in range(6)
            ]

        assert [r.status_code for r in responses] == [202] * 5 + [429]

    async def test_email_login_success_clears_account_failures(self, make_app: AppFactory) -> None:
        wrong = {"email": USER_EMAIL, "password": "Wrong000"}
        async with client_for(make_app(TRUSTED_PROXIES="1")) as client:
            for n in range(4):
                await client.post(LOGIN, json=wrong, headers=forwarded(n))
            await login_from(client, forwarded(4))
            after = [
                await client.post(LOGIN, json=wrong, headers=forwarded(5 + n)) for n in range(5)
            ]

        assert {error_of(r) for r in after} == {(401, "auth.invalidCredentials")}

    async def test_password_verify_counts_failures_per_user(self, make_app: AppFactory) -> None:
        async with client_for(make_app(RATE_LIMIT_DEFAULT="100/s,burst=100")) as client:
            headers = bearer((await login(client, USER_EMAIL))["accessToken"])

            async def change(current: str) -> int:
                body = {"currentPassword": current, "newPassword": PASSWORD}
                return (
                    await client.put("/api/v1/me/password", json=body, headers=headers)
                ).status_code

            cleared = [await change("Wrong000") for _ in range(4)] + [await change(PASSWORD)]
            failed = [await change("Wrong000") for _ in range(5)]
            locked = await change(PASSWORD)

        assert cleared == [422] * 4 + [200]
        assert failed == [422] * 5
        assert locked == 429

    async def test_refresh_counts_per_session_then_ip(self, make_app: AppFactory) -> None:
        refresh = "/api/v1/auth/refresh"
        async with client_for(make_app()) as client:
            first = await login(client, USER_EMAIL)
            second = await login(client, USER_EMAIL)
            token = first["refreshToken"]
            for _ in range(10):
                response = await client.post(refresh, json={"refreshToken": token})
                assert response.status_code == 200, response.text
                token = response.json()["data"]["refreshToken"]
            limited = await client.post(refresh, json={"refreshToken": token})
            other = await client.post(refresh, json={"refreshToken": second["refreshToken"]})
            unknown = [
                await client.post(refresh, json={"refreshToken": "x" * 43}) for _ in range(11)
            ]

        assert error_of(limited) == (429, "common.tooManyRequests")
        assert other.status_code == 200
        assert [r.status_code for r in unknown] == [401] * 10 + [429]


async def login_from(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    response = await client.post(
        LOGIN, json={"email": USER_EMAIL, "password": PASSWORD}, headers=headers
    )
    assert response.status_code == 200, response.text
