from typing import TYPE_CHECKING, Any

import pytest

from tests.parity.conftest import PG_CONTAINERS, Pair, TempUser, psql
from tests.parity.test_auth_parity import normalize

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.parity.conftest import Parity

URL = "/api/v1/addresses"
BEIJING = {
    "provinceCode": "110000000000",
    "cityCode": "110100000000",
    "districtCode": "110101000000",
}

type Tokens = tuple[str, str]


def purge(user: TempUser, *tables: str) -> None:
    """删除临时用户在两个后端数据库中的指定表数据。"""
    for container, user_id in zip(PG_CONTAINERS, user.ids, strict=True):
        sql = ";".join(f'DELETE FROM "{table}" WHERE "userId" = {user_id}' for table in tables)  # noqa: S608
        psql(container, sql)


def comparable(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: comparable(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [comparable(v) for v in value]
    if key in {"favoriteId", "userId", "deletedAt"} and value is not None:
        return f"<{type(value).__name__}>"
    return normalize(value, key)


def assert_same(pair: Pair) -> tuple[Any, Any]:
    python, nestjs = pair.both()
    assert python.status_code == nestjs.status_code, (python.text, nestjs.text)
    assert python.headers.get("content-type") == nestjs.headers.get("content-type")
    assert comparable(python.json()) == comparable(nestjs.json()), (python.text, nestjs.text)
    return python.json(), nestjs.json()


def send(
    parity: Parity,
    method: str,
    urls: str | tuple[str, str],
    pair_tokens: Tokens | None,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> Pair:
    """两个后端各自的 URL（路径中的 id 不同）与令牌发出同一请求。"""
    url_pair = (urls, urls) if isinstance(urls, str) else urls
    token_pair: tuple[str | None, str | None] = pair_tokens or (None, None)
    responses = [
        client.request(
            method,
            url,
            headers={**(headers or {}), **({"Authorization": f"Bearer {token}"} if token else {})},
            **kwargs,
        )
        for client, url, token in zip(parity.clients, url_pair, token_pair, strict=True)
    ]
    return Pair(*responses)


def ids(results: tuple[Any, Any]) -> tuple[int, int]:
    return results[0]["data"]["id"], results[1]["data"]["id"]


def item_urls(base: str, pair_ids: tuple[int, int], suffix: str = "") -> tuple[str, str]:
    return f"{base}/{pair_ids[0]}{suffix}", f"{base}/{pair_ids[1]}{suffix}"


@pytest.fixture(scope="module")
def user_tokens(shared_user: TempUser) -> Iterator[Tokens]:
    purge(shared_user, "address")
    yield shared_user.tokens
    purge(shared_user, "address")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", ""),
        ("POST", ""),
        ("GET", "/default"),
        ("GET", "/1"),
        ("PUT", "/1"),
        ("DELETE", "/1"),
        ("PUT", "/1/default"),
    ],
)
def test_requires_authentication(parity: Parity, method: str, path: str) -> None:
    assert_same(send(parity, method, URL + path, None, json={}))


@pytest.mark.parametrize("lang", ["en", "zh"])
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"name": "A", "phone": "1", "address": "x"},
        {"name": "A", "phone": "13800138000", "provinceCode": "110000000000", "address": "x"},
        {"name": "A", "phone": "13800138000", **BEIJING, "provinceCode": "1100", "address": "x"},
        {"name": 1, "phone": 2, "province": "", "city": "c" * 51, "district": "d", "address": ""},
        {"name": "A", "phone": "13800138000", **BEIJING, "address": "x", "isDefault": "yes"},
    ],
)
def test_create_validation(
    parity: Parity, user_tokens: Tokens, body: dict[str, Any], lang: str
) -> None:
    assert_same(send(parity, "POST", URL, user_tokens, {"Accept-Language": lang}, json=body))


@pytest.mark.parametrize(
    "query",
    [
        "?page=0&limit=abc",
        "?filters=%7Bbad",
        "?filters=%7B%22provinceCode%22%3A%22x%22%7D",
        "?sort=%5B%7B%22orderBy%22%3A%22x%22%2C%22order%22%3A%22up%22%7D%5D",
        "?sort=%7B%7D",
        "?page=1.5",
    ],
)
def test_list_validation(parity: Parity, user_tokens: Tokens, query: str) -> None:
    assert_same(send(parity, "GET", URL + query, user_tokens))


@pytest.mark.parametrize("path", ["/abc", "/0", "/2147483648", "/999999", "/999999/default"])
def test_unknown_or_invalid_id(parity: Parity, user_tokens: Tokens, path: str) -> None:
    for method in ("GET", "PUT", "DELETE"):
        if path.endswith("/default") and method != "PUT":
            continue
        assert_same(send(parity, method, URL + path, user_tokens, json={}))


def test_address_lifecycle(parity: Parity, user_tokens: Tokens) -> None:
    assert_same(send(parity, "GET", URL, user_tokens))
    assert_same(send(parity, "GET", URL + "/default", user_tokens))

    first = ids(
        assert_same(
            send(
                parity,
                "POST",
                URL,
                user_tokens,
                json={
                    "name": " <b>Ann</b> ",
                    "phone": "13800138000",
                    "province": "P",
                    "city": "C",
                    "district": "D",
                    "address": "<i>road</i> 1",
                    "unknown": True,
                },
            )
        )
    )
    second = ids(
        assert_same(
            send(
                parity,
                "POST",
                URL,
                user_tokens,
                json={
                    "name": "Bob",
                    "phone": "13900139000",
                    **BEIJING,
                    "province": "ignored",
                    "address": "x",
                    "isDefault": True,
                },
            )
        )
    )
    third = ids(
        assert_same(
            send(
                parity,
                "POST",
                URL,
                user_tokens,
                json={"name": "Cat", "phone": "13700137000", **BEIJING, "address": "y"},
            )
        )
    )
    assert_same(
        send(
            parity,
            "POST",
            URL,
            user_tokens,
            json={
                "name": "Bad",
                "phone": "13700137000",
                **BEIJING,
                "cityCode": "120100000000",
                "address": "z",
            },
        )
    )
    assert_same(send(parity, "GET", URL, user_tokens))
    assert_same(send(parity, "GET", URL + "/default", user_tokens))
    assert_same(send(parity, "GET", item_urls(URL, first), user_tokens))

    for query in (
        "?filters=%7B%22provinceCode%22%3A%22110000000000%22%7D",
        "?filters=%7B%22province%22%3A%22P%22%2C%22city%22%3A%22%22%7D",
        "?sort=%5B%7B%22orderBy%22%3A%22name%22%2C%22order%22%3A%22desc%22%7D%5D",
        "?sort=%5B%7B%22orderBy%22%3A%22id%22%2C%22order%22%3A%22ASC%22%7D%5D&page=2&limit=2",
        "?limit=60",
    ):
        assert_same(send(parity, "GET", URL + query, user_tokens))

    for body in (
        {"name": "Ann2", "isDefault": True},
        {"cityCode": "110100000000"},
        {**BEIJING, "cityCode": "120100000000"},
        {**BEIJING, "province": "kept?"},
        {"province": "NewP", "isDefault": False},
        {"phone": "123"},
        {},
    ):
        assert_same(send(parity, "PUT", item_urls(URL, first), user_tokens, json=body))

    assert_same(send(parity, "PUT", item_urls(URL, third, "/default"), user_tokens))
    assert_same(send(parity, "GET", URL, user_tokens))
    assert_same(send(parity, "DELETE", item_urls(URL, third), user_tokens))
    assert_same(send(parity, "GET", URL + "/default", user_tokens))
    assert_same(send(parity, "GET", item_urls(URL, third), user_tokens))
    assert_same(send(parity, "DELETE", item_urls(URL, third), user_tokens))
    assert_same(send(parity, "DELETE", item_urls(URL, first), user_tokens))
    assert_same(send(parity, "DELETE", item_urls(URL, second), user_tokens))
    assert_same(send(parity, "GET", URL, user_tokens))
    assert_same(send(parity, "GET", URL + "/default", user_tokens))


def test_address_limit(parity: Parity, shared_user: TempUser, user_tokens: Tokens) -> None:
    purge(shared_user, "address")
    body = {"name": "N", "phone": "13800138000", "province": "P", "city": "C", "district": "D"}
    for index in range(20):
        assert_same(send(parity, "POST", URL, user_tokens, json={**body, "address": str(index)}))
    for lang in ("en", "zh"):
        assert_same(
            send(
                parity,
                "POST",
                URL,
                user_tokens,
                {"Accept-Language": lang},
                json={**body, "address": "x"},
            )
        )
    assert_same(send(parity, "GET", URL + "?limit=5&page=3", user_tokens))
    purge(shared_user, "address")
