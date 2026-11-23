import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import parse_qsl
lazy from collections.abc import AsyncIterator

import httpx as httpx_lib
import pytest
from redis.asyncio import Redis
from sqlalchemy import text
lazy import httpx2 as httpx
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.identity.providers.sms import DAILY_LIMIT, FAILURE_LIMIT, MAX_ATTEMPTS
from app.identity.providers.sms.store import RedisSmsStore
from app.infrastructure.database.session import Database
from app.infrastructure.throttler import account_subject
from app.infrastructure.throttler.rules import AUTH_LOGIN_ACCOUNT
from tests.integration.conftest import _build_app, client_for, error_of, rules_of
from tests.integration.identity import USER_EMAIL, auth_headers, bearer

SEND = "/api/v1/auth/sms/send"
LOGIN = "/api/v1/auth/sms/login"
PHONE = "+8613800138000"
ACCOUNT_KEY = f"rl:auth-login-account:{account_subject(PHONE)}"


def sms_key(kind: str, *parts: str, phone: str = PHONE) -> str:
    return ":".join(("api", "sms", kind, *parts, f"{{{phone}}}"))


ALIYUN_ENV = {
    "SMS_PROVIDER": "aliyun",
    "SMS_ALIYUN_ACCESS_KEY_ID": "akid",
    "SMS_ALIYUN_ACCESS_KEY_SECRET": "aksecret",
    "SMS_ALIYUN_SIGN_NAME": "签名",
    "SMS_ALIYUN_TEMPLATE_CODE": "SMS_1",
}


class FakeAliyun:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.status = 200
        self.code = "OK"

    def __call__(self, request: httpx_lib.Request) -> httpx_lib.Response:
        assert request.url.host == "dysmsapi.aliyuncs.com"
        assert request.headers["x-acs-action"] == "SendSms"
        assert request.headers["authorization"].startswith("ACS3-HMAC-SHA256 Credential=akid,")
        assert request.url.query == b""
        assert (
            request.headers["x-acs-content-sha256"] == hashlib.sha256(request.content).hexdigest()
        )
        query = dict(parse_qsl(request.content.decode()))
        if self.status != 200 or self.code != "OK":
            return httpx_lib.Response(self.status, json={"Code": self.code, "Message": "x"})
        self.sent.append((query["PhoneNumbers"], json.loads(query["TemplateParam"])["code"]))
        return httpx_lib.Response(200, json={"Code": "OK", "BizId": "biz"})

    @property
    def last_code(self) -> str:
        return self.sent[-1][1]


@pytest.fixture
def aliyun() -> FakeAliyun:
    return FakeAliyun()


def sms_app(
    database: Database,
    aliyun: FakeAliyun,
    monkeypatch: pytest.MonkeyPatch,
    redis: Redis | None = None,
) -> FastAPI:
    for name, value in ALIYUN_ENV.items():
        monkeypatch.setenv(name, value)
    app = _build_app(database, redis=redis)
    app.state.context.http_transport = httpx_lib.MockTransport(aliyun)
    return app


@pytest.fixture
def app(
    connection: AsyncConnection, aliyun: FakeAliyun, monkeypatch: pytest.MonkeyPatch
) -> FastAPI:
    return sms_app(Database.bound_to(connection), aliyun, monkeypatch)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(app) as http:
        yield http


async def clear_cooldown(app: FastAPI, phone: str = PHONE) -> None:
    await app.state.context.sms_store.delete(sms_key("cooldown", phone=phone))


async def clear_rate_limits(redis: Redis) -> None:
    async for name in redis.scan_iter(match="rl:*"):
        await redis.delete(name)


async def send(
    client: httpx.AsyncClient,
    phone: str = PHONE,
    scene: str = "login",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await client.post(SEND, json={"phone": phone, "scene": scene}, headers=headers)


class TestSendSmsCode:
    async def test_sends_code_to_aliyun(
        self, client: httpx.AsyncClient, aliyun: FakeAliyun
    ) -> None:
        response = await send(client, "13800138000")

        assert response.status_code == 202
        assert response.json()["data"] is None
        assert aliyun.sent[0][0] == "13800138000"
        assert len(aliyun.last_code) == 6

    async def test_cooldown_is_429_with_retry_after(self, client: httpx.AsyncClient) -> None:
        await send(client)
        response = await send(client)

        assert error_of(response) == (429, "auth.sendTooFrequent")
        assert 0 < int(response.headers["retry-after"]) <= 60

    async def test_provider_failure_is_502_and_can_retry(
        self, client: httpx.AsyncClient, aliyun: FakeAliyun
    ) -> None:
        aliyun.code = "isv.BUSINESS_LIMIT_CONTROL"
        assert error_of(await send(client)) == (502, "common.badGateway")
        aliyun.status = 500
        assert error_of(await send(client)) == (502, "common.badGateway")

        aliyun.status, aliyun.code = 200, "OK"
        assert (await send(client)).status_code == 202

    async def test_bind_phone_scene_requires_jwt(self, client: httpx.AsyncClient) -> None:
        anonymous = await send(client, scene="bind_phone")
        invalid = await send(client, scene="bind_phone", headers=bearer("nope"))
        assert error_of(anonymous) == (401, "common.unauthorized")
        assert error_of(invalid) == (401, "common.unauthorized")
        assert invalid.headers["www-authenticate"] == 'Bearer error="invalid_token"'

        headers = await auth_headers(client)
        assert (await send(client, scene="bind_phone", headers=headers)).status_code == 202

    async def test_validation(self, client: httpx.AsyncClient) -> None:
        response = await client.post(SEND, json={"phone": "123", "scene": "reset"})
        assert rules_of(response) == {"phone": "phone", "scene": "enum"}


class TestLoginWithSms:
    async def test_unknown_phone_registers_then_logs_in(
        self, app: FastAPI, client: httpx.AsyncClient, aliyun: FakeAliyun
    ) -> None:
        await send(client)
        first = await client.post(LOGIN, json={"phone": "13800138000", "code": aliyun.last_code})
        assert first.status_code == 200, first.text
        user = first.json()["data"]["user"]
        assert (user["phone"], user["email"], user["hasPassword"]) == (PHONE, None, False)
        assert user["roles"] == ["user"]

        await clear_cooldown(app)
        await send(client)
        second = await client.post(LOGIN, json={"phone": PHONE, "code": aliyun.last_code})
        assert second.json()["data"]["user"]["id"] == user["id"]

    async def test_code_is_single_use(self, client: httpx.AsyncClient, aliyun: FakeAliyun) -> None:
        await send(client)
        body = {"phone": PHONE, "code": aliyun.last_code}
        assert (await client.post(LOGIN, json=body)).status_code == 200
        assert error_of(await client.post(LOGIN, json=body)) == (
            422,
            "auth.verificationCodeInvalid",
        )

    async def test_wrong_code_and_expired_attempts(
        self, app: FastAPI, client: httpx.AsyncClient, aliyun: FakeAliyun
    ) -> None:
        await send(client)
        code = aliyun.last_code
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(MAX_ATTEMPTS):
            response = await client.post(LOGIN, json={"phone": PHONE, "code": wrong})
            assert error_of(response) == (422, "auth.verificationCodeInvalid")
        limited = await client.post(LOGIN, json={"phone": PHONE, "code": code})
        assert error_of(limited) == (429, "common.tooManyRequests")

        await app.state.context.rate_limiter.reset(AUTH_LOGIN_ACCOUNT, account_subject(PHONE))
        response = await client.post(LOGIN, json={"phone": PHONE, "code": code})
        assert error_of(response) == (422, "auth.verificationCodeInvalid")

    async def test_successful_login_clears_account_failures(
        self, app: FastAPI, client: httpx.AsyncClient, aliyun: FakeAliyun
    ) -> None:
        await send(client)
        for _ in range(AUTH_LOGIN_ACCOUNT.quota.burst - 1):
            await client.post(LOGIN, json={"phone": PHONE, "code": "000000"})
        await client.post(LOGIN, json={"phone": PHONE, "code": aliyun.last_code})

        await clear_cooldown(app)
        await send(client)
        for _ in range(AUTH_LOGIN_ACCOUNT.quota.burst):
            response = await client.post(LOGIN, json={"phone": PHONE, "code": "000000"})
        assert response.status_code != 429

    async def test_code_format_is_validated(self, client: httpx.AsyncClient) -> None:
        response = await client.post(LOGIN, json={"phone": PHONE, "code": "12345"})
        assert rules_of(response) == {"code": "pattern"}

    async def test_disabled_account_is_403(
        self,
        client: httpx.AsyncClient,
        aliyun: FakeAliyun,
        connection: AsyncConnection,
    ) -> None:
        await connection.execute(
            text("UPDATE users SET phone = :phone, status = 'disabled' WHERE email = :email"),
            {"phone": PHONE, "email": USER_EMAIL},
        )
        await send(client)
        response = await client.post(LOGIN, json={"phone": PHONE, "code": aliyun.last_code})
        assert error_of(response) == (403, "auth.accountDisabled")


class TestBindPhone:
    async def test_binds_phone_with_bind_scene_code(
        self, app: FastAPI, client: httpx.AsyncClient, aliyun: FakeAliyun
    ) -> None:
        headers = await auth_headers(client)
        await send(client, scene="login")
        login_code = aliyun.last_code
        rejected = await client.put(
            "/api/v1/me/phone", json={"phone": PHONE, "code": login_code}, headers=headers
        )
        assert error_of(rejected) == (422, "auth.verificationCodeInvalid")

        await clear_cooldown(app)
        await send(client, scene="bind_phone", headers=headers)
        response = await client.put(
            "/api/v1/me/phone", json={"phone": PHONE, "code": aliyun.last_code}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["phone"] == PHONE
        me = await client.get("/api/v1/me", headers=headers)
        assert me.json()["data"]["phone"] == PHONE

    async def test_phone_of_other_account_is_409(
        self, app: FastAPI, client: httpx.AsyncClient, aliyun: FakeAliyun
    ) -> None:
        await send(client)
        await client.post(LOGIN, json={"phone": PHONE, "code": aliyun.last_code})

        headers = await auth_headers(client)
        await clear_cooldown(app)
        await send(client, scene="bind_phone", headers=headers)
        response = await client.put(
            "/api/v1/me/phone", json={"phone": PHONE, "code": aliyun.last_code}, headers=headers
        )
        assert error_of(response) == (409, "user.phoneTaken")

    async def test_locked_phone_is_429(self, app: FastAPI, client: httpx.AsyncClient) -> None:
        await app.state.context.sms_store.set(sms_key("locked"), "1", 60_000)
        response = await client.put(
            "/api/v1/me/phone",
            json={"phone": PHONE, "code": "123456"},
            headers=await auth_headers(client),
        )
        assert error_of(response) == (429, "auth.verificationLocked")
        assert 0 < int(response.headers["retry-after"]) <= 60

    async def test_requires_jwt(self, client: httpx.AsyncClient) -> None:
        response = await client.put("/api/v1/me/phone", json={"phone": PHONE, "code": "123456"})
        assert error_of(response) == (401, "common.unauthorized")


@pytest.mark.committed
class TestRedisStore:
    @pytest.fixture
    async def redis_app(
        self,
        committed_app: FastAPI,
        aliyun: FakeAliyun,
        monkeypatch: pytest.MonkeyPatch,
    ) -> FastAPI:
        context = committed_app.state.context
        app = sms_app(context.database, aliyun, monkeypatch, redis=context.redis)
        await context.redis.flushdb()
        return app

    async def test_code_cooldown_and_single_use(
        self, redis_app: FastAPI, aliyun: FakeAliyun
    ) -> None:
        redis: Redis = redis_app.state.context.redis
        async with client_for(redis_app) as client:
            await send(client)
            assert error_of(await send(client)) == (429, "auth.sendTooFrequent")
            assert 0 < await redis.pttl(sms_key("cooldown")) <= 60_000
            assert 0 < await redis.pttl(sms_key("code", "login")) <= 300_000

            body = {"phone": PHONE, "code": aliyun.last_code}
            assert (await client.post(LOGIN, json=body)).status_code == 200
            again = await client.post(LOGIN, json=body)
            assert error_of(again) == (422, "auth.verificationCodeInvalid")

    async def test_attempts_and_lock(self, redis_app: FastAPI, aliyun: FakeAliyun) -> None:
        redis: Redis = redis_app.state.context.redis
        async with client_for(redis_app) as client:
            await send(client)
            code = aliyun.last_code
            wrong = "000000" if code != "000000" else "111111"
            for _ in range(MAX_ATTEMPTS):
                await client.post(LOGIN, json={"phone": PHONE, "code": wrong})
            assert await redis.exists(sms_key("code", "login")) == 0
            assert await redis.exists(ACCOUNT_KEY) == 1
            for _ in range(FAILURE_LIMIT - MAX_ATTEMPTS):
                await clear_rate_limits(redis)
                await client.post(LOGIN, json={"phone": PHONE, "code": wrong})

            await clear_rate_limits(redis)
            locked = await client.post(LOGIN, json={"phone": PHONE, "code": code})
            assert error_of(locked) == (429, "auth.verificationLocked")
            assert 3500 < int(locked.headers["retry-after"]) <= 3600
            assert 3_500_000 < await redis.pttl(sms_key("locked")) <= 3_600_000

    async def test_daily_limit_and_send_failure_rollback(
        self, redis_app: FastAPI, aliyun: FakeAliyun
    ) -> None:
        redis: Redis = redis_app.state.context.redis
        daily = sms_key("daily", f"{datetime.now(UTC):%Y%m%d}")
        async with client_for(redis_app) as client:
            aliyun.code = "isv.BUSINESS_LIMIT_CONTROL"
            assert error_of(await send(client)) == (502, "common.badGateway")
            assert await redis.get(daily) == b"0"
            assert await redis.exists(sms_key("cooldown")) == 0
            assert 0 < await redis.pttl(daily) <= 25 * 3600 * 1000

            aliyun.code = "OK"
            await redis.set(daily, DAILY_LIMIT, keepttl=True)
            limited = await send(client)
            assert error_of(limited) == (429, "auth.sendTooFrequent")
            assert 0 < int(limited.headers["retry-after"]) <= 86_400
            assert await redis.get(daily) == str(DAILY_LIMIT).encode()

    async def test_expired_daily_key_is_not_recreated(self, redis_app: FastAPI) -> None:
        store = redis_app.state.context.sms_store
        await store.decrement("sms:daily:none")
        assert await redis_app.state.context.redis.exists("sms:daily:none") == 0


async def test_redis_failure_is_503(
    connection: AsyncConnection, aliyun: FakeAliyun, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = sms_app(Database.bound_to(connection), aliyun, monkeypatch)
    app.state.context.sms_store = RedisSmsStore(Redis.from_url("redis://127.0.0.1:1/0"))
    async with client_for(app) as client:
        for response in (
            await send(client),
            await client.post(LOGIN, json={"phone": PHONE, "code": "123456"}),
        ):
            assert error_of(response) == (503, "common.serviceUnavailable")
            assert response.headers["retry-after"] == "5"
    assert aliyun.sent == []
