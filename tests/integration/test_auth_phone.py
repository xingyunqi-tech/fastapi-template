import hashlib
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urlsplit
lazy from collections.abc import AsyncIterator

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy import text
lazy import httpx2
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.infrastructure.database.session import Database
from tests.integration.conftest import _build_app, client_for
from tests.integration.test_auth import bearer

SEND = "/api/v1/auth/phone/send-code"
SMS_LOGIN = "/api/v1/auth/phone/sms-login"
PHONE_LOGIN = "/api/v1/auth/phone/login"
REGISTER = "/api/v1/auth/phone/register"
RESET = "/api/v1/auth/phone/reset-password"
PHONE = "13800138000"


def md5(value: str) -> str:
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()


PASSWORD = md5("Secret00")

type AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch) -> AppFactory:
    def factory(redis: Redis | None = None, **env: str) -> FastAPI:
        monkeypatch.setenv("SMS_MOCK_MODE", "true")
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return _build_app(Database.bound_to(connection), redis=redis)

    return factory


@pytest.fixture
def app(make_app: AppFactory) -> FastAPI:
    return make_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    async with client_for(app) as http:
        yield http


async def stored(app: FastAPI, phone: str, type_: str = "login") -> Any:
    return await app.state.context.cache.get(f"sms:code:{type_}:{phone}")


async def stored_code(app: FastAPI, phone: str, type_: str = "login") -> str:
    value = await stored(app, phone, type_)
    assert value is not None
    return str(value["code"])


def other_code(code: str) -> str:
    return "000000" if code != "000000" else "111111"


async def register(
    app: FastAPI,
    client: httpx2.AsyncClient,
    phone: str = PHONE,
    password: str = PASSWORD,
    **extra: Any,
) -> dict[str, Any]:
    sent = await client.post(SEND, json={"phone": phone, "type": "register"})
    assert sent.json() == {"code": 200, "message": "success"}
    response = await client.post(
        REGISTER,
        json={
            "phone": phone,
            "code": await stored_code(app, phone, "register"),
            "password": password,
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def error(response: httpx2.Response) -> tuple[int, Any]:
    body = response.json()
    assert body["code"] == response.status_code
    return response.status_code, body["data"]


class TestSendCode:
    async def test_defaults_to_login_and_requires_registered_phone(
        self, app: FastAPI, client: httpx2.AsyncClient
    ) -> None:
        response = await client.post(SEND, json={"phone": PHONE})
        assert response.json() == {
            "code": 422,
            "message": "Validation failed",
            "data": {"phone": "Phone number is not registered"},
        }

    async def test_change_phone_is_unsupported(
        self, app: FastAPI, client: httpx2.AsyncClient
    ) -> None:
        response = await client.post(
            SEND, json={"phone": PHONE, "type": "change_phone"}, headers={"Accept-Language": "zh"}
        )
        assert response.json() == {
            "code": 422,
            "message": "数据验证失败",
            "data": {"type": "不支持的验证码类型"},
        }

    async def test_register_requires_unused_phone(
        self, app: FastAPI, client: httpx2.AsyncClient
    ) -> None:
        await register(app, client)
        response = await client.post(SEND, json={"phone": PHONE, "type": "register"})
        assert error(response) == (422, {"phone": "Phone number already exists"})

    async def test_resend_within_interval_is_rate_limited(
        self, app: FastAPI, client: httpx2.AsyncClient
    ) -> None:
        await register(app, client)
        assert (await client.post(SEND, json={"phone": PHONE})).status_code == 200
        response = await client.post(SEND, json={"phone": PHONE})
        status, data = error(response)
        assert status == 422
        assert data["phone"] == "Requests are too frequent, please try again later"
        assert isinstance(data["retryAfter"], int)
        assert 1 <= data["retryAfter"] <= 60

    async def test_code_has_configured_length(self, make_app: AppFactory) -> None:
        app = make_app(SMS_CODE_LENGTH="4")
        async with client_for(app) as client:
            await register(app, client)
            await client.post(SEND, json={"phone": PHONE})
            code = await stored_code(app, PHONE)
        assert len(code) == 4
        assert code.isdigit()

    async def test_send_failure_without_credentials(self, make_app: AppFactory) -> None:
        async with client_for(make_app(SMS_MOCK_MODE="false")) as client:
            response = await client.post(SEND, json={"phone": PHONE, "type": "register"})
        assert error(response) == (422, {"phone": "Failed to send verification code"})

    async def test_validation_errors(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        response = await client.post(SEND, json={"phone": "123", "type": "nope"})
        status, data = error(response)
        assert status == 422
        assert set(data) == {"phone", "type"}


class TestRedisKeys:
    async def test_code_and_send_lock_keys(self, make_app: AppFactory, redis_url: str) -> None:
        redis = Redis.from_url(redis_url)
        phone = "13900001111"
        try:
            await redis.delete(
                f"cache::sms:code:register:{phone}", f"lock:sms:send:register:{phone}"
            )
            async with client_for(make_app(redis=redis)) as client:
                response = await client.post(SEND, json={"phone": phone, "type": "register"})
                assert response.status_code == 200
            raw = await redis.get(f"cache::sms:code:register:{phone}")
            assert raw is not None
            stored = json.loads(raw)["value"]
            assert set(stored) == {"code", "attempts", "createdAt"}
            assert stored["attempts"] == 0
            assert 299_000 < await redis.pttl(f"cache::sms:code:register:{phone}") <= 300_000
            assert 59_000 < await redis.pttl(f"lock:sms:send:register:{phone}") <= 60_000
        finally:
            await redis.delete(
                f"cache::sms:code:register:{phone}", f"lock:sms:send:register:{phone}"
            )
            await redis.aclose()


class TestRegisterAndLogin:
    async def test_register_returns_tokens_for_active_user(
        self, app: FastAPI, client: httpx2.AsyncClient
    ) -> None:
        data = await register(app, client, nickname="  Johnny  ")
        assert list(data) == ["refreshToken", "token", "tokenExpires", "user"]
        user = data["user"]
        assert user["phone"] == PHONE
        assert user["provider"] == "phone"
        assert user["role"] == {"id": 2}
        assert user["status"] == {"id": 1}
        me = await client.get("/api/v1/auth/me", headers=bearer(data["token"]))
        assert me.json()["data"]["status"] == {
            "id": 1,
            "name": "Active",
            "__entity": "StatusEntity",
        }

    async def test_register_with_wrong_code(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        await client.post(SEND, json={"phone": PHONE, "type": "register"})
        wrong = other_code(await stored_code(app, PHONE, "register"))
        response = await client.post(
            REGISTER, json={"phone": PHONE, "code": wrong, "password": PASSWORD}
        )
        assert error(response) == (422, {"code": "Invalid verification code"})

    async def test_phone_password_login(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        await register(app, client)
        ok = await client.post(PHONE_LOGIN, json={"phone": PHONE, "password": PASSWORD.upper()})
        assert ok.status_code == 200
        assert ok.json()["data"]["user"]["role"] == {
            "id": 2,
            "name": "User",
            "__entity": "RoleEntity",
        }
        for body in (
            {"phone": PHONE, "password": md5("wrong")},
            {"phone": "13700000000", "password": PASSWORD},
        ):
            response = await client.post(PHONE_LOGIN, json=body)
            assert error(response) == (
                422,
                {"credentials": "Incorrect phone number or password"},
            )

    async def test_sms_login(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        await register(app, client)
        await client.post(SEND, json={"phone": PHONE})
        code = await stored_code(app, PHONE)
        response = await client.post(SMS_LOGIN, json={"phone": PHONE, "code": code})
        assert response.status_code == 200
        again = await client.post(SMS_LOGIN, json={"phone": PHONE, "code": code})
        assert error(again) == (422, {"code": "Invalid verification code"})

    async def test_sms_login_attempts_are_limited(
        self, app: FastAPI, client: httpx2.AsyncClient
    ) -> None:
        await register(app, client)
        await client.post(SEND, json={"phone": PHONE})
        code = await stored_code(app, PHONE)
        wrong = other_code(code)
        for _ in range(5):
            response = await client.post(SMS_LOGIN, json={"phone": PHONE, "code": wrong})
            assert error(response) == (422, {"code": "Invalid verification code"})
        assert (await stored(app, PHONE))["attempts"] == 5
        response = await client.post(SMS_LOGIN, json={"phone": PHONE, "code": code})
        assert error(response) == (422, {"code": "Invalid verification code"})
        assert await stored(app, PHONE) is None

    async def test_sms_login_unknown_phone(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        await app.state.context.cache.set(
            "sms:code:login:13700000000",
            {"code": "123456", "attempts": 0, "createdAt": 0},
            60_000,
        )
        response = await client.post(SMS_LOGIN, json={"phone": "13700000000", "code": "123456"})
        assert error(response) == (422, {"phone": "Not found"})

    async def test_blocked_user_cannot_log_in(
        self, app: FastAPI, client: httpx2.AsyncClient, connection: AsyncConnection
    ) -> None:
        data = await register(app, client)
        await connection.execute(
            text('UPDATE "user" SET "statusId" = NULL WHERE id = :id'), {"id": data["user"]["id"]}
        )
        response = await client.post(PHONE_LOGIN, json={"phone": PHONE, "password": PASSWORD})
        assert error(response) == (422, {"phone": "User is not active"})


class TestResetPassword:
    async def test_reset_revokes_sessions(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        data = await register(app, client)
        await client.post(SEND, json={"phone": PHONE, "type": "reset_password"})
        code = await stored_code(app, PHONE, "reset_password")
        new_password = md5("NewSecret01")
        response = await client.post(
            RESET, json={"phone": PHONE, "code": code, "password": new_password}
        )
        assert response.json() == {"code": 200, "message": "success"}
        me = await client.get("/api/v1/auth/me", headers=bearer(data["token"]))
        assert me.status_code == 401
        old = await client.post(PHONE_LOGIN, json={"phone": PHONE, "password": PASSWORD})
        assert old.status_code == 422
        new = await client.post(PHONE_LOGIN, json={"phone": PHONE, "password": new_password})
        assert new.status_code == 200

    async def test_reset_with_invalid_code(self, app: FastAPI, client: httpx2.AsyncClient) -> None:
        response = await client.post(
            RESET, json={"phone": PHONE, "code": "123456", "password": PASSWORD}
        )
        assert error(response) == (422, {"code": "Invalid verification code"})


class TestVerifyIdentity:
    async def passwordless_user(
        self, app: FastAPI, client: httpx2.AsyncClient, connection: AsyncConnection
    ) -> str:
        data = await register(app, client)
        await connection.execute(
            text('UPDATE "user" SET password = NULL WHERE id = :id'), {"id": data["user"]["id"]}
        )
        return str(data["token"])

    async def test_delete_account_with_sms_code(
        self, app: FastAPI, client: httpx2.AsyncClient, connection: AsyncConnection
    ) -> None:
        token = await self.passwordless_user(app, client, connection)
        missing = await client.request("DELETE", "/api/v1/auth/me", headers=bearer(token), json={})
        assert error(missing) == (422, {"smsCode": "SMS verification code is required"})
        wrong = await client.request(
            "DELETE", "/api/v1/auth/me", headers=bearer(token), json={"smsCode": "123456"}
        )
        assert error(wrong) == (422, {"code": "Invalid verification code"})

        sent = await client.post(SEND, json={"phone": PHONE, "type": "verify_identity"})
        assert sent.status_code == 200
        code = await stored_code(app, PHONE, "verify_identity")
        response = await client.request(
            "DELETE", "/api/v1/auth/me", headers=bearer(token), json={"smsCode": code}
        )
        assert response.json() == {"code": 200, "message": "success"}
        assert (
            await client.post(PHONE_LOGIN, json={"phone": PHONE, "password": PASSWORD})
        ).status_code == 422

    async def test_change_email_with_sms_code(
        self, app: FastAPI, client: httpx2.AsyncClient, connection: AsyncConnection
    ) -> None:
        token = await self.passwordless_user(app, client, connection)
        await client.post(SEND, json={"phone": PHONE, "type": "verify_identity"})
        code = await stored_code(app, PHONE, "verify_identity")
        response = await client.put(
            "/api/v1/auth/me",
            headers=bearer(token),
            json={"email": "phone.user@example.com", "smsCode": code},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["email"] is None


class TestAliyunProvider:
    async def test_sends_signed_request(self, make_app: AppFactory) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200, json={"Code": "OK", "Message": "OK", "BizId": "b", "RequestId": "r"}
            )

        app = make_app(
            SMS_MOCK_MODE="false",
            SMS_ACCESS_KEY_ID="AKID",
            SMS_ACCESS_KEY_SECRET="SECRET",
            SMS_SIGN_NAME="签名",
            SMS_TEMPLATE_CODE="SMS_1",
        )
        app.state.context.http_transport = httpx.MockTransport(handler)
        async with client_for(app) as client:
            response = await client.post(SEND, json={"phone": PHONE, "type": "register"})
            code = await stored_code(app, PHONE, "register")
        assert response.status_code == 200
        [request] = captured
        assert request.method == "POST"
        assert request.url.host == "dysmsapi.aliyuncs.com"
        query = dict(parse_qsl(urlsplit(str(request.url)).query))
        assert query == {
            "PhoneNumbers": PHONE,
            "SignName": "签名",
            "TemplateCode": "SMS_1",
            "TemplateParam": json.dumps({"code": code}, separators=(",", ":")),
        }
        assert request.headers["x-acs-action"] == "SendSms"
        assert request.headers["x-acs-version"] == "2017-05-25"
        assert request.headers["authorization"].startswith(
            "ACS3-HMAC-SHA256 Credential=AKID,SignedHeaders=host;x-acs-action;"
        )

    @pytest.mark.parametrize(
        "reply",
        [
            httpx.Response(200, json={"Code": "isv.BUSINESS_LIMIT_CONTROL", "Message": "limit"}),
            httpx.Response(400, json={"Code": "SignatureDoesNotMatch", "Message": "bad"}),
        ],
    )
    async def test_provider_failure_is_send_failed(
        self, make_app: AppFactory, reply: httpx.Response
    ) -> None:
        app = make_app(
            SMS_MOCK_MODE="false",
            SMS_ACCESS_KEY_ID="AKID",
            SMS_ACCESS_KEY_SECRET="SECRET",
            SMS_SIGN_NAME="签名",
            SMS_TEMPLATE_CODE="SMS_1",
        )
        app.state.context.http_transport = httpx.MockTransport(lambda _: reply)
        async with client_for(app) as client:
            first = await client.post(SEND, json={"phone": PHONE, "type": "register"})
            second = await client.post(SEND, json={"phone": PHONE, "type": "register"})
        assert error(first) == (422, {"phone": "Failed to send verification code"})
        assert error(second) == (422, {"phone": "Failed to send verification code"})
