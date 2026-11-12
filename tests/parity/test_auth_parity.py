import hashlib
import time
from typing import Any

import pytest

from tests.parity.conftest import Pair, Parity

VOLATILE_KEYS = {
    "token",
    "refreshToken",
    "tokenExpires",
    "createdAt",
    "updatedAt",
    "lastActiveAt",
    "ip",
}
RUN = str(int(time.time() * 1000))


def md5(text: str) -> str:
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


PASSWORD = md5("Secret00")


def normalize(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: normalize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if key in VOLATILE_KEYS and value is not None:
        return f"<{type(value).__name__}>"
    if key == "id" and isinstance(value, int):
        return "<id>"
    return value


def assert_same(pair: Pair) -> Any:
    python, nestjs = pair.both()
    assert python.status_code == nestjs.status_code, (python.text, nestjs.text)
    assert python.headers.get("content-type") == nestjs.headers.get("content-type")
    assert normalize(python.json()) == normalize(nestjs.json())
    return python.json()


def tokens(parity: Parity, email: str, password: str = PASSWORD) -> tuple[str, str]:
    pair = parity.request(
        "POST", "/api/v1/auth/email/login", json={"email": email, "password": password}
    )
    python, nestjs = (r.json()["data"]["token"] for r in pair.both())
    return python, nestjs


def authed(
    parity: Parity, method: str, url: str, pair_tokens: tuple[str, str], **kwargs: Any
) -> Pair:
    python_client, nestjs_client = parity.clients
    python = python_client.request(
        method, url, headers={"Authorization": f"Bearer {pair_tokens[0]}"}, **kwargs
    )
    nestjs = nestjs_client.request(
        method, url, headers={"Authorization": f"Bearer {pair_tokens[1]}"}, **kwargs
    )
    return Pair(python, nestjs)


@pytest.mark.parametrize("lang", ["en", "zh-CN"])
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "admin@example.com", "password": PASSWORD},
        {"email": " ADMIN@Example.com ", "password": PASSWORD.upper()},
        {"email": "missing@example.com", "password": PASSWORD},
        {"email": "admin@example.com", "password": md5("wrong")},
        {"email": "not-an-email", "password": "short"},
        {"email": 123, "password": None},
        {"email": "admin@example.com", "password": PASSWORD, "extra": True},
    ],
)
def test_email_login(parity: Parity, body: dict[str, Any], lang: str) -> None:
    assert_same(
        parity.request(
            "POST", "/api/v1/auth/email/login", json=body, headers={"Accept-Language": lang}
        )
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "john.doe@example.com", "password": PASSWORD, "firstName": "J", "lastName": "D"},
        {
            "email": f"parity.{RUN}@example.com",
            "password": PASSWORD,
            "firstName": "<b>P</b>",
            "lastName": "R",
        },
        {"email": "x@example.com", "password": PASSWORD, "firstName": "", "lastName": 1},
    ],
)
def test_email_register(parity: Parity, body: dict[str, Any]) -> None:
    assert_same(parity.request("POST", "/api/v1/auth/email/register", json=body))


@pytest.mark.parametrize(
    ("url", "body"),
    [
        ("/api/v1/auth/email/confirm", {"hash": "invalid"}),
        ("/api/v1/auth/email/confirm", {}),
        ("/api/v1/auth/email/confirm/new", {"hash": "a.b.c"}),
        ("/api/v1/auth/forgot/password", {"email": "missing@example.com"}),
        ("/api/v1/auth/forgot/password", {"email": "bad"}),
        ("/api/v1/auth/reset/password", {"hash": "invalid", "password": PASSWORD}),
        ("/api/v1/auth/reset/password", {"hash": "x", "password": "plain"}),
    ],
)
def test_email_link_errors(parity: Parity, url: str, body: dict[str, Any]) -> None:
    assert_same(parity.request("POST", url, json=body))


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer"},
    ],
)
@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "/api/v1/auth/me"),
        ("POST", "/api/v1/auth/refresh"),
        ("GET", "/api/v1/auth/sessions"),
        ("POST", "/api/v1/auth/logout"),
        ("GET", "/api/v1/users"),
        ("DELETE", "/api/v1/users/abc"),
    ],
)
def test_unauthenticated(parity: Parity, method: str, url: str, headers: dict[str, str]) -> None:
    assert_same(parity.request(method, url, headers={**headers, "Accept-Language": "zh"}))


def test_me_and_sessions(parity: Parity) -> None:
    pair_tokens = tokens(parity, "admin@example.com")
    assert_same(authed(parity, "GET", "/api/v1/auth/me", pair_tokens))
    python, nestjs = authed(parity, "GET", "/api/v1/auth/sessions", pair_tokens).both()
    assert python.status_code == nestjs.status_code == 200
    assert set(python.json()["data"][0]) == set(nestjs.json()["data"][0])


@pytest.mark.parametrize(
    "body",
    [
        {"firstName": 123, "password": md5("x")},
        {"password": md5("x"), "oldPassword": md5("wrong")},
        {"email": "john.doe@example.com"},
        {"gender": "male", "birthday": "1990/01/01"},
        {"photo": {"id": "not-uuid"}},
        {"photo": {"id": "3f0c8a52-7d0e-4b59-9f8e-2a1d6c4b7e10"}},
    ],
)
def test_update_me_errors(parity: Parity, body: dict[str, Any]) -> None:
    pair_tokens = tokens(parity, "john.doe@example.com")
    assert_same(authed(parity, "PUT", "/api/v1/auth/me", pair_tokens, json=body))


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"oldPassword": md5("wrong"), "newPassword": PASSWORD},
        {"oldPassword": "x", "newPassword": "y"},
    ],
)
def test_change_password_errors(parity: Parity, body: dict[str, Any]) -> None:
    pair_tokens = tokens(parity, "john.doe@example.com")
    assert_same(authed(parity, "PUT", "/api/v1/auth/password", pair_tokens, json=body))


def test_refresh_rotation(parity: Parity) -> None:
    pair = parity.request(
        "POST",
        "/api/v1/auth/email/login",
        json={"email": "john.doe@example.com", "password": PASSWORD},
    )
    refresh = tuple(r.json()["data"]["refreshToken"] for r in pair.both())
    rotated = authed(parity, "POST", "/api/v1/auth/refresh", refresh)
    assert_same(rotated)
    assert_same(authed(parity, "POST", "/api/v1/auth/refresh", refresh))


def test_revoke_unknown_session(parity: Parity) -> None:
    pair_tokens = tokens(parity, "john.doe@example.com")
    assert_same(authed(parity, "DELETE", "/api/v1/auth/sessions/999999", pair_tokens))
    assert_same(authed(parity, "DELETE", "/api/v1/auth/sessions/0", pair_tokens))


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/users?limit=2",
        "/api/v1/users?page=1&limit=1&sort=%5B%7B%22orderBy%22%3A%22id%22%2C%22order%22%3A%22desc%22%7D%5D",
        "/api/v1/users?limit=0",
        "/api/v1/users?page=abc",
        "/api/v1/users?filters=%7B%22roles%22%3A%5B%7B%22id%22%3A%22x%22%7D%5D%7D",
        "/api/v1/users?filters=notjson",
        "/api/v1/users/1",
        "/api/v1/users/999999",
        "/api/v1/users/2147483648",
    ],
)
def test_admin_reads(parity: Parity, url: str) -> None:
    pair_tokens = tokens(parity, "admin@example.com")
    assert_same(authed(parity, "GET", url, pair_tokens))


def test_users_forbidden_for_user_role(parity: Parity) -> None:
    pair_tokens = tokens(parity, "john.doe@example.com")
    assert_same(authed(parity, "GET", "/api/v1/users", pair_tokens))
    assert_same(authed(parity, "DELETE", "/api/v1/users/abc", pair_tokens))


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"email": "john.doe@example.com", "password": PASSWORD},
        {"email": f"admin.made.{RUN}@example.com", "role": {"id": 9}},
        {"email": f"admin.made.{RUN}@example.com", "status": {"id": 7}},
        {
            "email": f"admin.made.{RUN}@example.com",
            "photo": {"id": "3f0c8a52-7d0e-4b59-9f8e-2a1d6c4b7e10"},
        },
    ],
)
def test_admin_create_errors(parity: Parity, body: dict[str, Any]) -> None:
    pair_tokens = tokens(parity, "admin@example.com")
    assert_same(authed(parity, "POST", "/api/v1/users", pair_tokens, json=body))


def test_admin_create_update_delete(parity: Parity) -> None:
    pair_tokens = tokens(parity, "admin@example.com")
    body = {
        "email": f"lifecycle.{RUN}@example.com",
        "password": PASSWORD,
        "firstName": "Life",
        "role": {"id": 2},
        "status": {"id": 1},
        "birthday": "1990-05-06",
        "gender": 2,
    }
    created = authed(parity, "POST", "/api/v1/users", pair_tokens, json=body)
    assert_same(created)
    ids = tuple(r.json()["data"]["id"] for r in created.both())
    python_client, nestjs_client = parity.clients

    def both(method: str, suffix: str = "", **kwargs: Any) -> Pair:
        return Pair(
            python_client.request(
                method,
                f"/api/v1/users/{ids[0]}{suffix}",
                headers={"Authorization": f"Bearer {pair_tokens[0]}"},
                **kwargs,
            ),
            nestjs_client.request(
                method,
                f"/api/v1/users/{ids[1]}{suffix}",
                headers={"Authorization": f"Bearer {pair_tokens[1]}"},
                **kwargs,
            ),
        )

    assert_same(
        both("PUT", json={"nickname": "nick", "phone": f"139{RUN[-8:]}", "firstName": None})
    )
    assert_same(both("PUT", json={"email": "admin@example.com"}))
    assert_same(both("DELETE"))
    assert_same(both("GET"))
    assert_same(both("PUT", json={"nickname": "after"}))
