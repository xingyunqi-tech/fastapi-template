from typing import TYPE_CHECKING, Any

import pytest

from tests.parity.conftest import temp_user
from tests.parity.test_auth_parity import authed, tokens

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.parity.conftest import Pair, Parity

VOLATILE_KEYS = {"createdAt", "updatedAt", "readAt", "deletedAt", "sentAt"}
URL = "/api/v1/notifications"


def normalize(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: normalize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if key in VOLATILE_KEYS and value is not None:
        return "<time>"
    if key in {"id", "userId"} and isinstance(value, int):
        return "<id>"
    return value


def assert_same(pair: Pair) -> Any:
    python, nestjs = pair.both()
    assert python.status_code == nestjs.status_code, (python.text, nestjs.text)
    assert normalize(python.json()) == normalize(nestjs.json())
    return python.json()


@pytest.fixture(scope="module")
def fresh(parity: Parity) -> Iterator[tuple[str, str]]:
    with temp_user(parity, "notify") as user:
        yield user.tokens


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?page=2&limit=5",
        "?limit=500",
        "?page=0&limit=",
        "?category=ORDER&isRead=false",
        "?page=-1&limit=abc&category=NOPE&isRead=x",
        "?page=1.5",
    ],
)
def test_list(parity: Parity, fresh: tuple[str, str], query: str) -> None:
    assert_same(authed(parity, "GET", URL + query, fresh))


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/unread-count"),
        ("GET", "/999999"),
        ("GET", "/abc"),
        ("GET", "/0"),
        ("DELETE", "/999999"),
        ("DELETE", "/2147483648"),
        ("POST", "/read-all"),
        ("POST", "/999999/read"),
        ("POST", "/x/read"),
    ],
)
def test_single_operations(parity: Parity, fresh: tuple[str, str], method: str, path: str) -> None:
    assert_same(authed(parity, method, URL + path, fresh, headers={"Accept-Language": "zh"}))


def test_settings_lifecycle(parity: Parity, fresh: tuple[str, str]) -> None:
    listed = assert_same(authed(parity, "GET", f"{URL}/settings", fresh))
    assert {s["category"] for s in listed["data"]} == {
        "ORDER",
        "PAYMENT",
        "SYSTEM",
        "PROMOTION",
        "ACTIVITY",
        "STUDY",
    }
    body = {
        "category": "ORDER",
        "enableInApp": True,
        "enableEmail": True,
        "enableSms": True,
        "enablePush": True,
    }
    unchanged = authed(parity, "PUT", f"{URL}/settings", fresh, json=body)
    assert_same(unchanged)
    for response in unchanged.both():
        data = response.json()["data"]
        assert data["updatedAt"] == data["createdAt"]
    changed = authed(parity, "PUT", f"{URL}/settings", fresh, json={**body, "enableSms": False})
    assert_same(changed)
    for response in changed.both():
        data = response.json()["data"]
        assert data["updatedAt"] != data["createdAt"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"category": "NOPE", "enableInApp": "yes", "enableEmail": 1},
        {"category": "ORDER", "enableInApp": True, "enableEmail": True, "enableSms": True},
    ],
)
def test_settings_validation(parity: Parity, fresh: tuple[str, str], body: dict[str, Any]) -> None:
    assert_same(authed(parity, "PUT", f"{URL}/settings", fresh, json=body))


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", ""), ("GET", "/unread-count"), ("PUT", "/settings"), ("DELETE", "/abc")],
)
def test_unauthenticated(parity: Parity, method: str, path: str) -> None:
    assert_same(parity.request(method, URL + path))


def test_admin_list_shape(parity: Parity) -> None:
    pair_tokens = tokens(parity, "admin@example.com")
    python, nestjs = authed(parity, "GET", f"{URL}?limit=50", pair_tokens).both()
    assert python.status_code == nestjs.status_code == 200
    for response in (python, nestjs):
        for item in response.json()["data"]["list"]:
            keys = [
                "id",
                "userId",
                "type",
                "category",
                "title",
                "content",
                "metadata",
                "channels",
                "sentChannels",
                "isRead",
                "readAt",
                "createdAt",
                "updatedAt",
                "deletedAt",
            ]
            expected = [
                k
                for k in keys
                if not (k == "metadata" and "metadata" not in item)
                and not (k == "readAt" and "readAt" not in item)
            ]
            assert list(item) == expected
