from typing import TYPE_CHECKING, Any

import pytest

from tests.parity.test_addresses_parity import assert_same, ids, item_urls, purge, send

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.parity.conftest import Parity, TempUser

URL = "/api/v1/favorites"
UUID = "00000000-0000-4000-8000-00000000000A"

type Tokens = tuple[str, str]


@pytest.fixture(scope="module")
def user_tokens(shared_user: TempUser) -> Iterator[Tokens]:
    purge(shared_user, "favorite")
    yield shared_user.tokens
    purge(shared_user, "favorite")


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", ""), ("POST", ""), ("POST", "/check"), ("POST", "/toggle"), ("DELETE", "/1")],
)
def test_requires_authentication(parity: Parity, method: str, path: str) -> None:
    assert_same(send(parity, method, URL + path, None, json={}))


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize("path", ["", "/check", "/toggle"])
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"targetType": "movie", "targetId": "1"},
        {"targetType": "product", "targetId": "x" * 101},
        {
            "targetType": "product",
            "targetId": 1,
            "title": "t" * 201,
            "image": 2,
            "extra": "e" * 2001,
        },
    ],
)
def test_body_validation(
    parity: Parity, user_tokens: Tokens, path: str, body: dict[str, Any], lang: str
) -> None:
    assert_same(send(parity, "POST", URL + path, user_tokens, {"Accept-Language": lang}, json=body))


@pytest.mark.parametrize(
    "query",
    [
        "?targetType=bad&subjectId=x&page=0",
        "?page=1.5&limit=0",
        f"?subjectId={UUID}",
        "?targetType=question",
        "?limit=100",
    ],
)
def test_list_queries(parity: Parity, user_tokens: Tokens, query: str) -> None:
    assert_same(send(parity, "GET", URL + query, user_tokens))


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize(
    "body",
    [
        {"targetType": "question", "targetId": "q1"},
        {"targetType": "question", "targetId": UUID},
        {"targetType": "paper", "targetId": "p1"},
        {"targetType": "paper", "targetId": UUID},
    ],
)
def test_exam_targets_are_not_found(
    parity: Parity, user_tokens: Tokens, body: dict[str, Any], lang: str
) -> None:
    for path in ("", "/toggle"):
        assert_same(
            send(parity, "POST", URL + path, user_tokens, {"Accept-Language": lang}, json=body)
        )
    assert_same(send(parity, "POST", URL + "/check", user_tokens, json=body))


@pytest.mark.parametrize("path", ["/abc", "/0", "/999999", "/check"])
def test_delete_unknown_or_invalid_id(parity: Parity, user_tokens: Tokens, path: str) -> None:
    assert_same(send(parity, "DELETE", URL + path, user_tokens))


def test_favorite_lifecycle(parity: Parity, user_tokens: Tokens) -> None:
    product = {
        "targetType": "product",
        "targetId": "p1",
        "title": " <b>Phone</b> ",
        "image": "https://example.com/a.png",
        "extra": "{}",
        "unknown": 1,
    }
    first = ids(assert_same(send(parity, "POST", URL, user_tokens, json=product)))
    assert_same(
        send(parity, "POST", URL, user_tokens, json={"targetType": "article", "targetId": "a1"})
    )
    for lang in ("en", "zh"):
        assert_same(send(parity, "POST", URL, user_tokens, {"Accept-Language": lang}, json=product))
    assert_same(send(parity, "POST", URL + "/check", user_tokens, json=product))
    assert_same(
        send(
            parity,
            "POST",
            URL + "/check",
            user_tokens,
            json={"targetType": "store", "targetId": "s1"},
        )
    )
    store = {"targetType": "store", "targetId": "s1", "title": ""}
    assert_same(send(parity, "POST", URL + "/toggle", user_tokens, json=store))
    assert_same(send(parity, "GET", URL, user_tokens))
    assert_same(send(parity, "GET", URL + "?targetType=store&limit=1", user_tokens))
    assert_same(send(parity, "GET", URL + "?page=2&limit=2", user_tokens))
    assert_same(send(parity, "POST", URL + "/toggle", user_tokens, json=store))
    assert_same(send(parity, "DELETE", item_urls(URL, first), user_tokens))
    assert_same(send(parity, "DELETE", item_urls(URL, first), user_tokens))
    assert_same(send(parity, "GET", URL, user_tokens))
