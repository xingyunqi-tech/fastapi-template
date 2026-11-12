from typing import TYPE_CHECKING, Any

import pytest

from tests.parity.conftest import temp_user
from tests.parity.test_auth_parity import authed

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.parity.conftest import Pair, Parity

URL = "/api/v1/me/consents"


def normalize(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: normalize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if key == "agreedAt" and value is not None:
        return "<time>"
    return value


def assert_same(pair: Pair) -> Any:
    python, nestjs = pair.both()
    assert python.status_code == nestjs.status_code, (python.text, nestjs.text)
    assert normalize(python.json()) == normalize(nestjs.json())
    return python.json()


@pytest.fixture(scope="module")
def fresh(parity: Parity) -> Iterator[tuple[str, str]]:
    with temp_user(parity, "consent") as user:
        yield user.tokens


def test_status(parity: Parity, fresh: tuple[str, str]) -> None:
    assert_same(authed(parity, "GET", URL, fresh))


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"type": "PRIVACY_POLICY", "version": "2026-10-01"},
        {"type": "AI_SERVICE", "version": "v" * 51},
        {"type": "NOPE", "version": ""},
        {"type": 1, "version": 2},
        {"type": "USER_AGREEMENT", "version": "  "},
    ],
)
def test_agree(parity: Parity, fresh: tuple[str, str], body: dict[str, Any], lang: str) -> None:
    assert_same(authed(parity, "POST", URL, fresh, json=body, headers={"Accept-Language": lang}))


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_unauthenticated(parity: Parity, method: str) -> None:
    assert_same(parity.request(method, URL, json={}))
