import base64
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import text
lazy import httpx2
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.users.service import UsersService
from app.infrastructure.database.session import Database
from app.integrations.alipay import build_sign_string
from tests.integration.conftest import _build_app, client_for
from tests.integration.test_auth import bearer

WECHAT_LOGIN = "/api/v1/auth/wechat/login"
WECHAT_PHONE_LOGIN = "/api/v1/auth/wechat/phone-login"
ALIPAY_LOGIN = "/api/v1/auth/alipay/login"


class FakeWechat:
    """按 code 前缀返回结果的微信接口假实现。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.stable_token_calls: list[dict[str, Any]] = []
        self.invalid_tokens: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "api.weixin.qq.com"
        query = dict(request.url.params)
        path = request.url.path
        if path == "/sns/jscode2session":
            return self._code(query["js_code"], {"session_key": "sk"})
        if path == "/sns/oauth2/access_token":
            return self._code(query["code"], {"access_token": "user-token"})
        if path == "/sns/userinfo":
            assert query["access_token"] == "user-token"
            return httpx.Response(
                200, json={"openid": query["openid"], "nickname": "App 用户", "sex": 1}
            )
        if path == "/cgi-bin/stable_token":
            body = json.loads(request.content)
            self.stable_token_calls.append(body)
            return httpx.Response(
                200, json={"access_token": f"tok{len(self.stable_token_calls)}", "expires_in": 7200}
            )
        if path == "/wxa/business/getuserphonenumber":
            if query["access_token"] in self.invalid_tokens:
                return httpx.Response(200, json={"errcode": 40001, "errmsg": "invalid credential"})
            code = json.loads(request.content)["code"]
            if code == "used":
                return httpx.Response(200, json={"errcode": 40029, "errmsg": "invalid code"})
            country, _, number = code.partition("-")
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "phone_info": {
                        "phoneNumber": f"+{country}{number}",
                        "purePhoneNumber": number,
                        "countryCode": country,
                    },
                },
            )
        raise AssertionError(f"unexpected request {request.url}")

    @staticmethod
    def _code(code: str, extra: dict[str, Any]) -> httpx.Response:
        if code == "http500":
            return httpx.Response(500)
        if code == "bad":
            return httpx.Response(200, json={"errcode": 40029, "errmsg": "invalid code"})
        if code == "no-openid":
            return httpx.Response(200, json=extra)
        open_id, _, union_id = code.partition(":")
        body: dict[str, Any] = {"openid": open_id, **extra}
        if union_id:
            body["unionid"] = union_id
        return httpx.Response(200, json=body)


type AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch) -> AppFactory:
    def factory(handler: Callable[[httpx.Request], httpx.Response], **env: str) -> FastAPI:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        app = _build_app(Database.bound_to(connection))
        app.state.context.http_transport = httpx.MockTransport(handler)
        return app

    return factory


WECHAT_ENV = {
    "WECHAT_MINI_APP_ID": "wx-mini",
    "WECHAT_MINI_APP_SECRET": "mini-secret",
    "WECHAT_APP_ID": "wx-app",
    "WECHAT_APP_SECRET": "app-secret",
}


@pytest.fixture
def wechat() -> FakeWechat:
    return FakeWechat()


@pytest.fixture
def wechat_app(make_app: AppFactory, wechat: FakeWechat) -> FastAPI:
    return make_app(wechat, **WECHAT_ENV)


def error(response: httpx2.Response) -> tuple[int, str, Any]:
    body = response.json()
    assert body["code"] == response.status_code
    return response.status_code, body["message"], body["data"]


class TestWechatLogin:
    async def test_mini_app_creates_user_once(
        self, wechat_app: FastAPI, wechat: FakeWechat
    ) -> None:
        async with client_for(wechat_app) as client:
            first = await client.post(
                WECHAT_LOGIN, json={"code": "openid-1:union-1", "type": "mini_app"}
            )
            second = await client.post(
                WECHAT_LOGIN, json={"code": "openid-1", "type": "mini_app", "nickname": "N"}
            )
        assert first.status_code == 200, first.text
        user = first.json()["data"]["user"]
        assert user["wechatOpenId"] == "openid-1"
        assert user["wechatUnionId"] == "union-1"
        assert user["nickname"] == "微信用户"
        assert user["provider"] == "wechat"
        assert user["gender"] == 0
        assert user["role"] == {"id": 2}
        assert user["status"] == {"id": 1}
        again = second.json()["data"]["user"]
        assert again["id"] == user["id"]
        assert again["role"] == {"id": 2, "name": "User", "__entity": "RoleEntity"}
        request = wechat.requests[0]
        assert dict(request.url.params) == {
            "appid": "wx-mini",
            "secret": "mini-secret",
            "js_code": "openid-1:union-1",
            "grant_type": "authorization_code",
        }

    async def test_app_login_uses_wechat_profile(self, wechat_app: FastAPI) -> None:
        async with client_for(wechat_app) as client:
            response = await client.post(WECHAT_LOGIN, json={"code": "app-openid", "type": "app"})
        user = response.json()["data"]["user"]
        assert user["nickname"] == "App 用户"
        assert user["gender"] == 1
        assert user["wechatOpenId"] == "app-openid"

    async def test_nickname_from_request_wins(self, wechat_app: FastAPI) -> None:
        async with client_for(wechat_app) as client:
            response = await client.post(
                WECHAT_LOGIN, json={"code": "app-openid", "type": "app", "nickname": "Mine"}
            )
        assert response.json()["data"]["user"]["nickname"] == "Mine"

    @pytest.mark.parametrize(
        ("code", "data"),
        [
            (
                "bad",
                {"code": "Invalid verification code", "wechatError": "invalid code"},
            ),
            ("http500", {"wechat": "Request to the third-party service failed"}),
        ],
    )
    async def test_wechat_errors(self, wechat_app: FastAPI, code: str, data: Any) -> None:
        async with client_for(wechat_app) as client:
            response = await client.post(WECHAT_LOGIN, json={"code": code, "type": "mini_app"})
        assert error(response) == (422, "Validation failed", data)

    async def test_not_configured(self, make_app: AppFactory, wechat: FakeWechat) -> None:
        async with client_for(make_app(wechat)) as client:
            response = await client.post(WECHAT_LOGIN, json={"code": "x", "type": "app"})
        assert error(response) == (
            422,
            "Validation failed",
            {"wechat": "Service is not configured"},
        )
        assert wechat.requests == []

    async def test_blocked_user(self, wechat_app: FastAPI, connection: AsyncConnection) -> None:
        async with client_for(wechat_app) as client:
            first = await client.post(WECHAT_LOGIN, json={"code": "blocked", "type": "mini_app"})
            await connection.execute(
                text('UPDATE "user" SET "statusId" = NULL WHERE id = :id'),
                {"id": first.json()["data"]["user"]["id"]},
            )
            response = await client.post(WECHAT_LOGIN, json={"code": "blocked", "type": "mini_app"})
        assert error(response) == (422, "Validation failed", {"wechat": "User is not active"})


class TestWechatPhoneLogin:
    async def test_creates_user_with_phone_and_caches_token(
        self, wechat_app: FastAPI, wechat: FakeWechat
    ) -> None:
        async with client_for(wechat_app) as client:
            first = await client.post(
                WECHAT_PHONE_LOGIN, json={"loginCode": "o-phone", "phoneCode": "86-13800138000"}
            )
            second = await client.post(
                WECHAT_PHONE_LOGIN, json={"loginCode": "o-phone", "phoneCode": "86-13800138000"}
            )
        assert first.status_code == 200, first.text
        user = first.json()["data"]["user"]
        assert (user["phone"], user["wechatOpenId"]) == ("13800138000", "o-phone")
        assert second.json()["data"]["user"]["id"] == user["id"]
        assert wechat.stable_token_calls == [
            {
                "grant_type": "client_credential",
                "appid": "wx-mini",
                "secret": "mini-secret",
                "force_refresh": False,
            }
        ]
        cached = await wechat_app.state.context.cache.get("wechat:mini_app:access_token")
        assert cached == "tok1"

    async def test_refreshes_invalid_access_token(
        self, wechat_app: FastAPI, wechat: FakeWechat
    ) -> None:
        await wechat_app.state.context.cache.set("wechat:mini_app:access_token", "stale", 60_000)
        wechat.invalid_tokens.add("stale")
        async with client_for(wechat_app) as client:
            response = await client.post(
                WECHAT_PHONE_LOGIN, json={"loginCode": "o-r", "phoneCode": "86-13800138001"}
            )
        assert response.status_code == 200
        assert [call["force_refresh"] for call in wechat.stable_token_calls] == [False]

    async def test_binds_phone_to_existing_wechat_user(self, wechat_app: FastAPI) -> None:
        async with client_for(wechat_app) as client:
            created = await client.post(WECHAT_LOGIN, json={"code": "o-bind", "type": "mini_app"})
            assert created.json()["data"]["user"]["phone"] is None
            response = await client.post(
                WECHAT_PHONE_LOGIN, json={"loginCode": "o-bind", "phoneCode": "86-13800138002"}
            )
        user = response.json()["data"]["user"]
        assert user["id"] == created.json()["data"]["user"]["id"]
        assert user["phone"] == "13800138002"
        assert user["role"]["__entity"] == "RoleEntity"

    async def test_binds_wechat_to_existing_phone_user(
        self, wechat_app: FastAPI, connection: AsyncConnection
    ) -> None:
        user_id = await connection.scalar(
            text(
                'INSERT INTO "user" (phone, provider, "roleId", "statusId") '
                "VALUES ('13800138003', 'phone', 2, 1) RETURNING id"
            )
        )
        async with client_for(wechat_app) as client:
            response = await client.post(
                WECHAT_PHONE_LOGIN,
                json={"loginCode": "o-existing:u-existing", "phoneCode": "86-13800138003"},
            )
        user = response.json()["data"]["user"]
        assert user["id"] == user_id
        assert (user["wechatOpenId"], user["wechatUnionId"]) == ("o-existing", "u-existing")

    async def test_missing_openid_leaves_wechat_unbound(
        self, wechat_app: FastAPI, connection: AsyncConnection
    ) -> None:
        user_id = await connection.scalar(
            text(
                'INSERT INTO "user" (phone, provider, "roleId", "statusId") '
                "VALUES ('13800138009', 'phone', 2, 1) RETURNING id"
            )
        )
        async with client_for(wechat_app) as client:
            response = await client.post(
                WECHAT_PHONE_LOGIN, json={"loginCode": "no-openid", "phoneCode": "86-13800138009"}
            )
        assert response.json()["data"]["user"]["id"] == user_id
        stored = await connection.scalar(
            text('SELECT "wechatOpenId" FROM "user" WHERE id = :id'), {"id": user_id}
        )
        assert stored is None

    async def test_phone_bound_to_other_wechat(self, wechat_app: FastAPI) -> None:
        async with client_for(wechat_app) as client:
            await client.post(
                WECHAT_PHONE_LOGIN, json={"loginCode": "o-a", "phoneCode": "86-13800138004"}
            )
            response = await client.post(
                WECHAT_PHONE_LOGIN,
                json={"loginCode": "o-b", "phoneCode": "86-13800138004"},
                headers={"Accept-Language": "zh"},
            )
        assert error(response) == (
            422,
            "该手机号已绑定其他微信账号，请使用验证码登录",
            {"phone": "该手机号已绑定其他微信账号"},
        )

    async def test_rejects_non_mainland_phone(self, wechat_app: FastAPI) -> None:
        async with client_for(wechat_app) as client:
            response = await client.post(
                WECHAT_PHONE_LOGIN, json={"loginCode": "o-x", "phoneCode": "1-2025550100"}
            )
        assert error(response) == (
            422,
            "Only mainland China phone numbers are supported",
            {"phone": "Only mainland China phone numbers are supported"},
        )

    async def test_invalid_phone_code(self, wechat_app: FastAPI) -> None:
        async with client_for(wechat_app) as client:
            response = await client.post(
                WECHAT_PHONE_LOGIN, json={"loginCode": "o-x", "phoneCode": "used"}
            )
        assert error(response) == (
            422,
            "Phone number authorization expired, please try again",
            {"phoneCode": "Invalid verification code"},
        )

    async def test_passwordless_user_needs_sms_code_to_delete(self, wechat_app: FastAPI) -> None:
        async with client_for(wechat_app) as client:
            login = await client.post(
                WECHAT_PHONE_LOGIN, json={"loginCode": "o-del", "phoneCode": "86-13800138005"}
            )
            token = login.json()["data"]["token"]
            response = await client.request(
                "DELETE", "/api/v1/auth/me", headers=bearer(token), json={}
            )
        assert error(response) == (
            422,
            "Validation failed",
            {"smsCode": "SMS verification code is required"},
        )


def _rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeAlipay:
    def __init__(self, public_key: rsa.RSAPublicKey) -> None:
        self.public_key = public_key
        self.params: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "openapi.alipay.com"
        assert request.url.path == "/gateway.do"
        params = dict(parse_qsl(request.url.query.decode()))
        self.params.append(params)
        signature = base64.b64decode(params.pop("sign"))
        self.public_key.verify(
            signature, build_sign_string(params).encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        code = params["code"]
        if code == "http500":
            return httpx.Response(500)
        if code.startswith("isv") or code.startswith("isp"):
            return httpx.Response(
                200,
                json={
                    "error_response": {
                        "code": "40002",
                        "msg": "Invalid Arguments",
                        "sub_code": f"{code.replace('_', '.')}",
                    }
                },
            )
        if code == "no-openid":
            return httpx.Response(
                200, json={"alipay_system_oauth_token_response": {"user_id": "2088"}}
            )
        return httpx.Response(
            200,
            json={
                "alipay_system_oauth_token_response": {
                    "open_id": code,
                    "access_token": "t",
                    "expires_in": 1,
                }
            },
        )


@pytest.fixture
def alipay_key() -> rsa.RSAPrivateKey:
    return _rsa_key()


@pytest.fixture
def alipay(alipay_key: rsa.RSAPrivateKey) -> FakeAlipay:
    return FakeAlipay(alipay_key.public_key())


@pytest.fixture
def alipay_app(make_app: AppFactory, alipay: FakeAlipay, alipay_key: rsa.RSAPrivateKey) -> FastAPI:
    pem = alipay_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    body = "".join(line for line in pem.splitlines() if "PRIVATE KEY" not in line)
    return make_app(alipay, ALIPAY_APP_ID="2021000000000000", ALIPAY_PRIVATE_KEY=body)


class TestAlipayLogin:
    async def test_creates_user_once(self, alipay_app: FastAPI, alipay: FakeAlipay) -> None:
        async with client_for(alipay_app) as client:
            first = await client.post(ALIPAY_LOGIN, json={"authCode": "ali-open-1"})
            second = await client.post(ALIPAY_LOGIN, json={"authCode": "ali-open-1"})
        assert first.status_code == 200, first.text
        user = first.json()["data"]["user"]
        assert (user["alipayOpenId"], user["nickname"], user["provider"]) == (
            "ali-open-1",
            "支付宝用户",
            "alipay",
        )
        assert user["status"] == {"id": 1}
        assert second.json()["data"]["user"]["id"] == user["id"]
        params = alipay.params[0]
        assert {k: params[k] for k in ("app_id", "method", "format", "charset", "sign_type")} == {
            "app_id": "2021000000000000",
            "method": "alipay.system.oauth.token",
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": "RSA2",
        }
        assert params["grant_type"] == "authorization_code"
        assert params["version"] == "1.0"
        assert len(params["timestamp"]) == 19

    @pytest.mark.parametrize(
        ("code", "message", "data"),
        [
            (
                "isv_code-invalid",
                "Alipay authorization expired, please try again",
                {"authCode": "Invalid verification code"},
            ),
            (
                "isp_unknown-error",
                "Validation failed",
                {"alipay": "Request to the third-party service failed"},
            ),
            (
                "http500",
                "Validation failed",
                {"alipay": "Request to the third-party service failed"},
            ),
            (
                "no-openid",
                "Validation failed",
                {"alipay": "Request to the third-party service failed"},
            ),
        ],
    )
    async def test_errors(self, alipay_app: FastAPI, code: str, message: str, data: Any) -> None:
        async with client_for(alipay_app) as client:
            response = await client.post(ALIPAY_LOGIN, json={"authCode": code})
        assert error(response) == (422, message, data)

    async def test_not_configured(self, make_app: AppFactory, alipay: FakeAlipay) -> None:
        async with client_for(make_app(alipay)) as client:
            response = await client.post(ALIPAY_LOGIN, json={"authCode": "x"})
        assert error(response) == (
            422,
            "Validation failed",
            {"alipay": "Service is not configured"},
        )
        assert alipay.params == []


class TestConcurrentFirstLogin:
    @pytest.mark.parametrize(
        ("lookup", "url", "body", "column"),
        [
            (
                "find_by_wechat_open_id",
                WECHAT_LOGIN,
                {"code": "race-openid", "type": "mini_app"},
                "wechatOpenId",
            ),
            ("find_by_alipay_open_id", ALIPAY_LOGIN, {"authCode": "race-openid"}, "alipayOpenId"),
        ],
    )
    async def test_unique_violation_falls_back_to_existing_user(
        self,
        make_app: AppFactory,
        alipay: FakeAlipay,
        alipay_key: rsa.RSAPrivateKey,
        connection: AsyncConnection,
        monkeypatch: pytest.MonkeyPatch,
        lookup: str,
        url: str,
        body: dict[str, str],
        column: str,
    ) -> None:
        existing = await connection.scalar(
            text(
                f'INSERT INTO "user" ("{column}", provider, "roleId", "statusId") '  # noqa: S608
                "VALUES ('race-openid', 'wechat', 2, 1) RETURNING id"
            )
        )
        original = getattr(UsersService, lookup)
        calls = 0

        async def first_miss(self: UsersService, open_id: str | None) -> Any:
            nonlocal calls
            calls += 1
            return None if calls == 1 else await original(self, open_id)

        monkeypatch.setattr(UsersService, lookup, first_miss)
        pem = alipay_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        wechat = FakeWechat()

        def handler(request: httpx.Request) -> httpx.Response:
            return alipay(request) if "alipay" in request.url.host else wechat(request)

        app = make_app(handler, ALIPAY_APP_ID="2021", ALIPAY_PRIVATE_KEY=pem, **WECHAT_ENV)
        async with client_for(app) as client:
            response = await client.post(url, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["user"]["id"] == existing
        assert calls == 2
