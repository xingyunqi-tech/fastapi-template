from typing import TYPE_CHECKING, Any

import pytest

from tests.parity.conftest import config_exists, delete_configs
from tests.parity.test_auth_parity import authed, tokens

if TYPE_CHECKING:
    from tests.parity.conftest import Pair, Parity

URL = "/api/v1/app-profile"
ADMIN_URL = "/api/v1/admin/app-profile"
CONFIG_KEY = "app.profile"


def assert_same(pair: Pair) -> Any:
    python, nestjs = pair.both()
    assert python.status_code == nestjs.status_code, (python.text, nestjs.text)
    assert python.json() == nestjs.json()
    return python.json()


def test_get(parity: Parity) -> None:
    assert_same(parity.request("GET", URL))


@pytest.mark.parametrize(
    "body",
    [
        {"servicePhone": "call me", "serviceEmail": "bad", "appName": "x" * 21},
        {"appName": 1, "slogan": ["a"], "shareTitle": {"a": 1}},
        {"servicePhone": "   -  ", "serviceEmail": "  a@b.co ", "icpLicense": "x" * 51},
        {"copyright": "c" * 101, "serviceHours": "h" * 41, "companyName": "n" * 61},
    ],
)
def test_update_validation(parity: Parity, body: dict[str, Any]) -> None:
    assert_same(authed(parity, "PUT", ADMIN_URL, tokens(parity, "admin@example.com"), json=body))


def test_update_permissions(parity: Parity) -> None:
    assert_same(authed(parity, "PUT", ADMIN_URL, tokens(parity, "john.doe@example.com"), json={}))
    assert_same(parity.request("PUT", ADMIN_URL, json={}))


def test_update_and_restore(parity: Parity) -> None:
    pair_tokens = tokens(parity, "admin@example.com")
    existed = config_exists(CONFIG_KEY)
    original = [r.json()["data"] for r in parity.request("GET", URL).both()]
    try:
        assert_same(
            authed(
                parity,
                "PUT",
                ADMIN_URL,
                pair_tokens,
                json={
                    "appName": "  备考  ",
                    "slogan": "   ",
                    "shareTitle": None,
                    "servicePhone": " +86 400-123 4567 ",
                    "serviceEmail": "help@example.com",
                    "unknown": "x",
                },
            )
        )
        assert_same(parity.request("GET", URL))
        assert_same(authed(parity, "PUT", ADMIN_URL, pair_tokens, json={"slogan": "新"}))
        assert_same(parity.request("GET", URL))
    finally:
        for client, token, data in zip(parity.clients, pair_tokens, original, strict=True):
            client.put(
                ADMIN_URL, json=data, headers={"Authorization": f"Bearer {token}"}
            ).raise_for_status()
        delete_configs(CONFIG_KEY, sides=(not existed[0], not existed[1]))
