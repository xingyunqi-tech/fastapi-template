import time
from typing import TYPE_CHECKING, Any

import pytest

from tests.parity.conftest import config_exists, delete_configs
from tests.parity.test_auth_parity import authed, tokens

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.parity.conftest import Pair, Parity

VOLATILE_KEYS = {"createdAt", "updatedAt"}
URL = "/api/v1/config"
RUN = str(int(time.time() * 1000))


def normalize(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: normalize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if key in VOLATILE_KEYS and value is not None:
        return "<time>"
    return value


def assert_same(pair: Pair) -> Any:
    python, nestjs = pair.both()
    assert python.status_code == nestjs.status_code, (python.text, nestjs.text)
    assert normalize(python.json()) == normalize(nestjs.json())
    return python.json()


@pytest.fixture(scope="module", autouse=True)
def drop_run_configs() -> Iterator[None]:
    yield
    delete_configs(f"parity.{RUN}", f"parity.lifecycle.{RUN}")


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize(
    "key",
    [
        "ai.enabled",
        "ai.quota.chat",
        "practice.trialSize",
        "ai.ratePerMinute",
        "ai.model",
        "app.profile",
        "consent.privacyPolicyVersion",
        "unknown.key",
        "%E4%B8%AD",
    ],
)
def test_public_get(parity: Parity, key: str, lang: str) -> None:
    assert_same(parity.request("GET", f"{URL}/{key}", headers={"Accept-Language": lang}))


@pytest.mark.parametrize(
    "key", ["ai.ratePerMinute", "ai.enabled", "practice.dailySetSize", "ai.model", "unknown.key"]
)
def test_admin_get(parity: Parity, key: str) -> None:
    assert_same(authed(parity, "GET", f"{URL}/admin/{key}", tokens(parity, "admin@example.com")))


def test_requires_permission(parity: Parity) -> None:
    john = tokens(parity, "john.doe@example.com")
    assert_same(authed(parity, "GET", f"{URL}/admin/ai.enabled", john))
    assert_same(authed(parity, "PUT", f"{URL}/parity.denied", john, json={"value": {}}))
    assert_same(parity.request("GET", f"{URL}/admin/ai.enabled"))
    assert_same(parity.request("PUT", f"{URL}/parity.denied", json={"value": {}}))


@pytest.mark.parametrize(
    ("key", "body"),
    [
        ("app.profile", {"value": {}}),
        (f"parity.{RUN}", {}),
        (f"parity.{RUN}", {"value": "x", "isPublic": "yes"}),
        (f"parity.{RUN}", {"value": [1], "isPublic": None}),
        (f"parity.{RUN}", {"value": None}),
    ],
)
def test_upsert_errors(parity: Parity, key: str, body: dict[str, Any]) -> None:
    pair_tokens = tokens(parity, "admin@example.com")
    assert_same(authed(parity, "PUT", f"{URL}/{key}", pair_tokens, json=body))


def test_upsert_lifecycle(parity: Parity) -> None:
    pair_tokens = tokens(parity, "admin@example.com")
    key = f"parity.lifecycle.{RUN}"
    assert_same(parity.request("GET", f"{URL}/{key}"))
    created = assert_same(
        authed(
            parity,
            "PUT",
            f"{URL}/{key}",
            pair_tokens,
            json={"value": {"b": 1, "a": {"y": 2, "x": 1}}},
        )
    )
    assert created["data"]["isPublic"] is False
    assert_same(parity.request("GET", f"{URL}/{key}"))
    assert_same(authed(parity, "GET", f"{URL}/admin/{key}", pair_tokens))
    same = authed(
        parity, "PUT", f"{URL}/{key}", pair_tokens, json={"value": {"a": {"x": 1, "y": 2}, "b": 1}}
    )
    assert_same(same)
    for response in same.both():
        data = response.json()["data"]
        assert data["updatedAt"] == data["createdAt"]
    published = authed(
        parity, "PUT", f"{URL}/{key}", pair_tokens, json={"value": {"c": True}, "isPublic": True}
    )
    assert_same(published)
    assert_same(parity.request("GET", f"{URL}/{key}"))
    kept = authed(parity, "PUT", f"{URL}/{key}", pair_tokens, json={"value": {"c": False}})
    assert kept.python.json()["data"]["isPublic"] is True
    assert_same(kept)
    assert_same(parity.request("GET", f"{URL}/{key}"))


def test_upsert_registered_key_restores_original(parity: Parity) -> None:
    pair_tokens = tokens(parity, "admin@example.com")
    key = "ai.ratePerMinute"
    existed = config_exists(key)
    original = [
        r.json()["data"] for r in authed(parity, "GET", f"{URL}/admin/{key}", pair_tokens).both()
    ]
    try:
        assert_same(
            authed(
                parity,
                "PUT",
                f"{URL}/{key}",
                pair_tokens,
                json={"value": {"value": 11}, "isPublic": True},
            )
        )
        assert_same(parity.request("GET", f"{URL}/{key}"))
        assert_same(authed(parity, "GET", f"{URL}/admin/{key}", pair_tokens))
    finally:
        for client, token, data in zip(parity.clients, pair_tokens, original, strict=True):
            client.put(
                f"{URL}/{key}",
                json={"value": data["value"]},
                headers={"Authorization": f"Bearer {token}"},
            ).raise_for_status()
        delete_configs(key, sides=(not existed[0], not existed[1]))
