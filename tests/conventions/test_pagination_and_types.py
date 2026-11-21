"""约定 §3 分页与排序、§4 数据类型。"""

lazy from fastapi.testclient import TestClient

from app.common.pagination import encode_cursor
from tests.conventions.probe import PROBE


def test_page_defaults(client: TestClient) -> None:
    data = client.get(f"{PROBE}/page").json()["data"]

    assert list(data) == ["items", "total", "page", "pageSize"]
    assert (data["total"], data["page"], data["pageSize"]) == (3, 1, 20)


def test_page_window(client: TestClient) -> None:
    data = client.get(f"{PROBE}/page", params={"page": 2, "pageSize": 2}).json()["data"]

    assert [item["id"] for item in data["items"]] == [1]
    assert (data["page"], data["pageSize"]) == (2, 2)


def test_page_size_bounds(client: TestClient) -> None:
    for value, rule in ((0, "min"), (101, "max")):
        response = client.get(f"{PROBE}/page", params={"pageSize": value})
        [item] = response.json()["data"]["errors"]
        assert (response.status_code, item["field"], item["rule"]) == (422, "query.pageSize", rule)


def test_sort(client: TestClient) -> None:
    data = client.get(f"{PROBE}/page", params={"sort": "-id"}).json()["data"]

    assert [item["id"] for item in data["items"]] == [3, 2, 1]


def test_sort_outside_allow_list(client: TestClient) -> None:
    response = client.get(f"{PROBE}/page", params={"sort": "password"})

    [item] = response.json()["data"]["errors"]
    assert (response.status_code, item["field"], item["rule"]) == (422, "query.sort", "enum")


def test_cursor_walks_pages(client: TestClient) -> None:
    first = client.get(f"{PROBE}/cursor", params={"limit": 3}).json()["data"]
    assert list(first) == ["items", "nextCursor"]
    assert [item["id"] for item in first["items"]] == [1, 2, 3]

    last = client.get(f"{PROBE}/cursor", params={"limit": 3, "cursor": first["nextCursor"]}).json()[
        "data"
    ]
    assert [item["id"] for item in last["items"]] == [4, 5]
    assert last["nextCursor"] is None


def test_invalid_cursor(client: TestClient) -> None:
    response = client.get(f"{PROBE}/cursor", params={"cursor": "%%%"})

    [item] = response.json()["data"]["errors"]
    assert (response.status_code, item["field"], item["rule"]) == (422, "query.cursor", "format")


def test_type_serialization(client: TestClient) -> None:
    data = client.get(f"{PROBE}/types").json()["data"]

    assert data == {
        "id": "019a8f3c-6b1e-7d2a-9c4f-1e2d3c4b5a69",
        "createdAt": "2026-11-17T16:00:00.000Z",
        "birthday": "1990-05-06",
        "amount": 1999,
        "color": "light_blue",
        "note": None,
    }


def test_input_normalization(client: TestClient) -> None:
    response = client.post(
        f"{PROBE}/echo",
        json={
            "email": "  A.B@Example.COM ",
            "phone": "13800000000",
            "name": "abc",
            "at": "2026-11-17T16:00:00+08:00",
        },
    )

    data = response.json()["data"]
    assert (data["email"], data["phone"], data["at"]) == (
        "a.b@example.com",
        "+8613800000000",
        "2026-11-17T08:00:00.000Z",
    )


def test_e164_phone_is_kept(client: TestClient) -> None:
    response = client.post(
        f"{PROBE}/echo", json={"email": "a@b.co", "name": "abc", "phone": "+447911123456"}
    )

    assert response.json()["data"]["phone"] == "+447911123456"


def test_page_upper_bound(client: TestClient) -> None:
    response = client.get(f"{PROBE}/page", params={"page": 2**31})

    [item] = response.json()["data"]["errors"]
    assert (response.status_code, item["field"], item["rule"]) == (422, "query.page", "max")


def test_empty_sort_uses_default(client: TestClient) -> None:
    data = client.get(f"{PROBE}/page", params={"sort": ""}).json()["data"]

    assert [item["id"] for item in data["items"]] == [3, 2, 1]


def test_cursor_with_wrong_element_types(client: TestClient) -> None:
    response = client.get(f"{PROBE}/cursor", params={"cursor": encode_cursor("a", "b")})

    [item] = response.json()["data"]["errors"]
    assert (response.status_code, item["field"], item["rule"]) == (422, "query.cursor", "format")
