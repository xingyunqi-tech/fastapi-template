from typing import TYPE_CHECKING, Any

import pytest

from tests.parity.conftest import PG_CONTAINERS, psql
from tests.parity.test_addresses_parity import assert_same, ids, item_urls, purge, send
from tests.parity.test_auth_parity import tokens

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.parity.conftest import Parity, TempUser

URL = "/api/v1/feedbacks"
ADMIN = "admin@example.com"

type Tokens = tuple[str, str]


@pytest.fixture(scope="module")
def user_tokens(shared_user: TempUser) -> Iterator[Tokens]:
    purge(shared_user, "feedback")
    yield shared_user.tokens
    purge(shared_user, "feedback")


@pytest.mark.parametrize(("method", "path"), [("GET", ""), ("POST", ""), ("GET", "/1")])
def test_requires_authentication(parity: Parity, method: str, path: str) -> None:
    assert_same(send(parity, method, URL + path, None, json={}))


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"type": "question_error", "content": ""},
        {"type": "bug", "content": "c" * 2001, "contact": 1},
        {"type": "bug", "content": "c", "images": [1, "a", "b", "c", "d", "e"]},
        {"type": "bug", "content": "c", "images": "a"},
        {"type": "bug", "content": "c", "images": ["x" * 1001]},
    ],
)
def test_create_validation(
    parity: Parity, user_tokens: Tokens, body: dict[str, Any], lang: str
) -> None:
    assert_same(send(parity, "POST", URL, user_tokens, {"Accept-Language": lang}, json=body))


@pytest.mark.parametrize(
    "query",
    [
        "?type=ai_content&status=x&page=0&limit=a",
        "?page=1e400",
        "?type=question_error",
        "?status=closed",
    ],
)
def test_list_validation_and_filters(parity: Parity, user_tokens: Tokens, query: str) -> None:
    assert_same(send(parity, "GET", URL + query, user_tokens))


@pytest.mark.parametrize("path", ["/abc", "/0", "/999999"])
def test_unknown_or_invalid_id(parity: Parity, user_tokens: Tokens, path: str) -> None:
    assert_same(send(parity, "GET", URL + path, user_tokens))


def test_feedback_lifecycle(parity: Parity, user_tokens: Tokens) -> None:
    first = ids(
        assert_same(send(parity, "POST", URL, user_tokens, json={"type": "bug", "content": "c"}))
    )
    assert_same(
        send(
            parity,
            "POST",
            URL,
            user_tokens,
            json={
                "type": "suggestion",
                "content": "<b>s</b>",
                "images": ["a", "b"],
                "contact": "x",
                "unknown": 1,
            },
        )
    )
    assert_same(
        send(parity, "POST", URL, user_tokens, json={"type": "other", "content": "o", "images": []})
    )
    assert_same(send(parity, "GET", item_urls(URL, first), user_tokens))
    for query in (
        "",
        "?type=bug",
        "?status=pending&limit=2",
        "?page=1.3&limit=1",
        "?page=2&limit=1.5",
        "?limit=100",
    ):
        assert_same(send(parity, "GET", URL + query, user_tokens))
    admin = tokens(parity, ADMIN)
    assert_same(send(parity, "GET", item_urls(URL, first), admin))


def insert_feedback(user: TempUser, type_: str) -> tuple[int, int]:
    columns = '"userId", "type", "content", "questionVersion", "reason"'
    python, nestjs = (
        int(
            psql(
                container,
                f'INSERT INTO "feedback" ({columns}) '  # noqa: S608
                f"VALUES ({user_id}, '{type_}', 'r', 3, 'other') RETURNING id",
            ).split()[0]
        )
        for container, user_id in zip(PG_CONTAINERS, user.ids, strict=True)
    )
    return python, nestjs


def test_report_types_visibility(
    parity: Parity, shared_user: TempUser, user_tokens: Tokens
) -> None:
    question_error = insert_feedback(shared_user, "question_error")
    ai_content = insert_feedback(shared_user, "ai_content")
    assert_same(send(parity, "GET", item_urls(URL, question_error), user_tokens))
    assert_same(send(parity, "GET", item_urls(URL, ai_content), user_tokens))
    assert_same(send(parity, "GET", URL, user_tokens))
    assert_same(send(parity, "GET", URL + "?type=question_error", user_tokens))
