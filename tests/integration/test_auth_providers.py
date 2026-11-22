import asyncio
import base64
import json
from typing import Any
from urllib.parse import parse_qsl
lazy from collections.abc import AsyncIterator, Callable

import httpx as httpx_lib
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy import text
lazy import httpx2 as httpx
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.identity.providers.alipay import build_sign_string
from app.infrastructure.database.session import Database
from tests import keys
from tests.integration.conftest import _build_app, client_for, error_of, rules_of
from tests.integration.identity import auth_headers, bearer

PROVIDERS = "/api/v1/auth/providers"

WECHAT_ENV = {
    "WECHAT_MINIAPP_APP_ID": "wx-mini",
    "WECHAT_MINIAPP_SECRET": "mini-secret",
    "WECHAT_APP_APP_ID": "wx-app",
    "WECHAT_APP_SECRET": "app-secret",
}


def alipay_env() -> dict[str, str]:
    return {
        "ALIPAY_APP_ID": "ali-app",
        "ALIPAY_PRIVATE_KEY": keys.alipay_app_pair()[0],
        "ALIPAY_PUBLIC_KEY": keys.alipay_platform_pair()[1],
    }


def login_path(provider: str) -> str:
    return f"{PROVIDERS}/{provider}/login"


def _code(code: str, extra: dict[str, Any]) -> httpx_lib.Response:
    """`openid[:unionid]`；`bad` 无效，`http500`、`busy` 上游出错，`no-openid` 缺少 openid。"""
    if code == "http500":
        return httpx_lib.Response(500)
    if code == "bad":
        return httpx_lib.Response(200, json={"errcode": 40029, "errmsg": "invalid code"})
    if code == "busy":
        return httpx_lib.Response(200, json={"errcode": -1, "errmsg": "system busy"})
    if code == "no-openid":
        return httpx_lib.Response(200, json=extra)
    openid, _, union_id = code.partition(":")
    body: dict[str, Any] = {"openid": openid, **extra}
    if union_id:
        body["unionid"] = union_id
    return httpx_lib.Response(200, json=body)


class FakeWechat:
    def __init__(self) -> None:
        self.stable_tokens = 0
        self.invalid_tokens: set[str] = set()

    def __call__(self, request: httpx_lib.Request) -> httpx_lib.Response:
        query = dict(request.url.params)
        path = request.url.path
        if path == "/sns/jscode2session":
            assert (query["appid"], query["secret"]) == ("wx-mini", "mini-secret")
            return _code(query["js_code"], {"session_key": "sk"})
        if path == "/sns/oauth2/access_token":
            assert (query["appid"], query["secret"]) == ("wx-app", "app-secret")
            return _code(query["code"], {"access_token": "user-token"})
        if path == "/cgi-bin/stable_token":
            self.stable_tokens += 1
            return httpx_lib.Response(
                200, json={"access_token": f"tok{self.stable_tokens}", "expires_in": 7200}
            )
        if path == "/wxa/business/getuserphonenumber":
            if query["access_token"] in self.invalid_tokens:
                return httpx_lib.Response(200, json={"errcode": 40001, "errmsg": "invalid"})
            code = json.loads(request.content)["code"]
            if code == "bad":
                return httpx_lib.Response(200, json={"errcode": 40029, "errmsg": "invalid code"})
            if code == "malformed":
                return httpx_lib.Response(200, json={"errcode": 0, "phone_info": "x"})
            country, _, number = code.partition("-")
            return httpx_lib.Response(
                200,
                json={
                    "errcode": 0,
                    "phone_info": {"purePhoneNumber": number, "countryCode": country},
                },
            )
        raise AssertionError(f"unexpected request {request.url}")


class FakeAlipay:
    """校验请求签名，用平台私钥签名响应。"""

    def __init__(self) -> None:
        self.tamper = False
        self.append_forged = False

    def __call__(self, request: httpx_lib.Request) -> httpx_lib.Response:
        assert request.url.host == "openapi.alipay.com"
        assert request.url.query == b""
        params = dict(parse_qsl(request.content.decode()))
        signature = base64.b64decode(params.pop("sign"))
        app_public = serialization.load_pem_public_key(keys.alipay_app_pair()[1].encode())
        assert isinstance(app_public, rsa.RSAPublicKey)
        app_public.verify(
            signature, build_sign_string(params).encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        assert (params["method"], params["app_id"]) == ("alipay.system.oauth.token", "ali-app")
        code = params["code"]
        if code == "bad":
            return httpx_lib.Response(
                200,
                json={"error_response": {"code": "40002", "sub_code": "isv.code-invalid"}},
            )
        if code == "denied":
            return httpx_lib.Response(
                200, json={"error_response": {"code": "40006", "sub_code": "isv.insufficient"}}
            )
        member = "user_id" if code.startswith("2088") else "open_id"
        content = json.dumps({member: code, "access_token": "t"}, separators=(",", ":"))
        key = serialization.load_pem_private_key(
            keys.alipay_platform_pair()[0].encode(), password=None
        )
        assert isinstance(key, rsa.RSAPrivateKey)
        sign = base64.b64encode(
            key.sign(content.encode(), padding.PKCS1v15(), hashes.SHA256())
        ).decode()
        if self.tamper:
            content = content.replace(code, f"{code}x")
        body = f'{{"alipay_system_oauth_token_response":{content},"sign":"{sign}"}}'
        if self.append_forged:
            forged = '"alipay_system_oauth_token_response":{"open_id":"victim"}'
            body = body[:-1] + f",{forged}}}"
        return httpx_lib.Response(
            200, content=body.encode(), headers={"content-type": "application/json"}
        )


class Upstream:
    def __init__(self) -> None:
        self.wechat = FakeWechat()
        self.alipay = FakeAlipay()

    def __call__(self, request: httpx_lib.Request) -> httpx_lib.Response:
        if request.url.host == "api.weixin.qq.com":
            return self.wechat(request)
        return self.alipay(request)


type AppFactory = Callable[..., FastAPI]


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


def provider_app(
    database: Database, upstream: Upstream, monkeypatch: pytest.MonkeyPatch, **env: str
) -> FastAPI:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = _build_app(database)
    app.state.context.http_transport = httpx_lib.MockTransport(upstream)
    return app


@pytest.fixture
def app(
    connection: AsyncConnection, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> FastAPI:
    return provider_app(
        Database.bound_to(connection), upstream, monkeypatch, **WECHAT_ENV, **alipay_env()
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(app) as http:
        yield http


async def provider_login(client: httpx.AsyncClient, provider: str, code: str) -> httpx.Response:
    return await client.post(login_path(provider), json={"code": code})


async def user_id(client: httpx.AsyncClient, provider: str, code: str) -> str:
    response = await provider_login(client, provider, code)
    assert response.status_code == 200, response.text
    value: str = response.json()["data"]["user"]["id"]
    return value


class TestDisabled:
    @pytest.fixture
    def app(
        self, connection: AsyncConnection, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
    ) -> FastAPI:
        return provider_app(Database.bound_to(connection), upstream, monkeypatch)

    async def test_no_provider_is_listed(self, client: httpx.AsyncClient) -> None:
        response = await client.get(PROVIDERS)
        assert (response.status_code, response.json()["data"]) == (200, [])

    @pytest.mark.parametrize("provider", ["wechat_miniapp", "wechat_app", "alipay", "github"])
    async def test_login_is_404(self, client: httpx.AsyncClient, provider: str) -> None:
        response = await provider_login(client, provider, "o1")
        assert error_of(response) == (404, "auth.providerDisabled")

    async def test_bind_wechat_phone_is_404(self, client: httpx.AsyncClient) -> None:
        response = await client.put(
            "/api/v1/me/phone/wechat",
            json={"code": "86-13800138000"},
            headers=await auth_headers(client),
        )
        assert error_of(response) == (404, "auth.providerDisabled")


async def test_enabled_providers_are_listed(client: httpx.AsyncClient) -> None:
    response = await client.get(PROVIDERS)
    assert response.json()["data"] == [
        {"id": "wechat_miniapp"},
        {"id": "wechat_app"},
        {"id": "alipay"},
    ]


async def test_partially_configured_provider_is_disabled(
    connection: AsyncConnection, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = alipay_env()
    del env["ALIPAY_PUBLIC_KEY"]
    app = provider_app(
        Database.bound_to(connection), upstream, monkeypatch, WECHAT_APP_APP_ID="wx", **env
    )
    async with client_for(app) as client:
        assert (await client.get(PROVIDERS)).json()["data"] == []


class TestWechat:
    async def test_first_login_registers_and_second_reuses(
        self, client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        response = await provider_login(client, "wechat_miniapp", "m1")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        user = data["user"]
        assert (user["email"], user["phone"], user["hasPassword"]) == (None, None, False)
        assert user["roles"] == ["user"]
        assert data["tokenType"] == "Bearer"
        assert await user_id(client, "wechat_miniapp", "m1") == user["id"]
        rows = await connection.execute(
            text("SELECT provider, openid, union_id FROM user_identities")
        )
        assert rows.all() == [("wechat_miniapp", "m1", None)]

    async def test_union_id_is_backfilled_and_links_providers(
        self, client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        first = await user_id(client, "wechat_miniapp", "m1")
        assert await user_id(client, "wechat_miniapp", "m1:u1") == first
        assert await user_id(client, "wechat_app", "a1:u1") == first
        rows = await connection.execute(
            text("SELECT provider, openid, union_id FROM user_identities ORDER BY provider")
        )
        assert rows.all() == [("wechat_app", "a1", "u1"), ("wechat_miniapp", "m1", "u1")]

    async def test_same_openid_on_other_provider_is_other_account(
        self, client: httpx.AsyncClient
    ) -> None:
        assert await user_id(client, "wechat_miniapp", "o1") != await user_id(
            client, "wechat_app", "o1"
        )

    @pytest.mark.parametrize("provider", ["wechat_miniapp", "wechat_app"])
    async def test_upstream_errors(self, client: httpx.AsyncClient, provider: str) -> None:
        assert error_of(await provider_login(client, provider, "bad")) == (
            422,
            "auth.providerCodeInvalid",
        )
        for code in ("http500", "busy", "no-openid"):
            assert error_of(await provider_login(client, provider, code)) == (
                502,
                "common.badGateway",
            )

    async def test_disabled_account_is_403(
        self, client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        user = await user_id(client, "wechat_miniapp", "m1")
        await connection.execute(
            text("UPDATE users SET status = 'disabled' WHERE id = :id"), {"id": user}
        )
        response = await provider_login(client, "wechat_miniapp", "m1")
        assert error_of(response) == (403, "auth.accountDisabled")

    async def test_code_is_required(self, client: httpx.AsyncClient) -> None:
        response = await client.post(login_path("wechat_app"), json={})
        assert rules_of(response) == {"code": "required"}

    async def test_deleted_account_releases_identity(self, client: httpx.AsyncClient) -> None:
        response = await provider_login(client, "wechat_miniapp", "m1:u1")
        data = response.json()["data"]
        deleted = await client.delete("/api/v1/me", headers=bearer(data["accessToken"]))
        assert deleted.status_code == 202
        assert await user_id(client, "wechat_app", "a1:u1") != data["user"]["id"]


class TestBindPhoneWithWechat:
    async def test_binds_phone(self, client: httpx.AsyncClient) -> None:
        headers = await auth_headers(client)
        response = await client.put(
            "/api/v1/me/phone/wechat", json={"code": "86-13800138000"}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["phone"] == "+8613800138000"

    async def test_conflict_and_invalid_code(self, client: httpx.AsyncClient) -> None:
        other = await provider_login(client, "wechat_miniapp", "m1")
        other_headers = bearer(other.json()["data"]["accessToken"])
        path = "/api/v1/me/phone/wechat"
        assert (
            await client.put(path, json={"code": "86-13800138000"}, headers=other_headers)
        ).status_code == 200

        headers = await auth_headers(client)
        taken = await client.put(path, json={"code": "86-13800138000"}, headers=headers)
        assert error_of(taken) == (409, "user.phoneTaken")
        invalid = await client.put(path, json={"code": "bad"}, headers=headers)
        assert error_of(invalid) == (422, "auth.providerCodeInvalid")
        malformed = await client.put(path, json={"code": "malformed"}, headers=headers)
        assert error_of(malformed) == (502, "common.badGateway")

    async def test_access_token_is_cached_and_refreshed(
        self, client: httpx.AsyncClient, upstream: Upstream
    ) -> None:
        headers = await auth_headers(client)
        path = "/api/v1/me/phone/wechat"
        await client.put(path, json={"code": "86-13800138000"}, headers=headers)
        await client.put(path, json={"code": "86-13800138001"}, headers=headers)
        assert upstream.wechat.stable_tokens == 1

        upstream.wechat.invalid_tokens.add("tok1")
        response = await client.put(path, json={"code": "86-13800138002"}, headers=headers)
        assert response.json()["data"]["phone"] == "+8613800138002"
        assert upstream.wechat.stable_tokens == 2


class TestAlipay:
    async def test_login_verifies_signatures(
        self, client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        first = await user_id(client, "alipay", "0681")
        assert await user_id(client, "alipay", "0681") == first
        rows = await connection.execute(text("SELECT provider, openid FROM user_identities"))
        assert rows.all() == [("alipay", "0681")]

    async def test_errors(self, client: httpx.AsyncClient, upstream: Upstream) -> None:
        assert error_of(await provider_login(client, "alipay", "bad")) == (
            422,
            "auth.providerCodeInvalid",
        )
        assert error_of(await provider_login(client, "alipay", "denied")) == (
            502,
            "common.badGateway",
        )
        upstream.alipay.tamper = True
        assert error_of(await provider_login(client, "alipay", "0681")) == (
            502,
            "common.badGateway",
        )

    async def test_legacy_user_id_is_used_as_openid(
        self, client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        await user_id(client, "alipay", "2088001")
        rows = await connection.execute(text("SELECT openid FROM user_identities"))
        assert rows.scalars().all() == ["2088001"]

    async def test_duplicate_response_member_is_rejected(
        self, client: httpx.AsyncClient, upstream: Upstream, connection: AsyncConnection
    ) -> None:
        upstream.alipay.append_forged = True
        response = await provider_login(client, "alipay", "0681")
        assert error_of(response) == (502, "common.badGateway")
        assert await connection.scalar(text("SELECT count(*) FROM user_identities")) == 0


@pytest.mark.committed
async def test_concurrent_first_login_creates_one_account(
    committed_app: FastAPI, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = provider_app(committed_app.state.context.database, upstream, monkeypatch, **WECHAT_ENV)
    async with client_for(app) as client:
        responses = await asyncio.gather(
            *(provider_login(client, "wechat_miniapp", "race") for _ in range(4))
        )
    assert [r.status_code for r in responses] == [200] * 4
    assert len({r.json()["data"]["user"]["id"] for r in responses}) == 1


@pytest.mark.committed
async def test_concurrent_union_id_link_reuses_account(
    committed_app: FastAPI, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = provider_app(committed_app.state.context.database, upstream, monkeypatch, **WECHAT_ENV)
    async with client_for(app) as client:
        owner = await user_id(client, "wechat_miniapp", "m1:u1")
        responses = await asyncio.gather(
            *(provider_login(client, "wechat_app", "a1:u1") for _ in range(4))
        )
    assert [r.status_code for r in responses] == [200] * 4
    assert {r.json()["data"]["user"]["id"] for r in responses} == {owner}
