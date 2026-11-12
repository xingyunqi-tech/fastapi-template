from typing import Any

from sqlalchemy import text
lazy import httpx2 as httpx
lazy import pytest
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.favorites.account_cleaner import FavoritesAccountCleaner
from app.modules.favorites.service import FavoritesService
from tests.integration.test_addresses import auth

URL = "/api/v1/favorites"
PRODUCT = {"targetType": "product", "targetId": "p1"}
UUID = "00000000-0000-4000-8000-00000000000A"


async def favorite(
    client: httpx.AsyncClient, headers: dict[str, str], **body: Any
) -> dict[str, Any]:
    response = await client.post(URL, headers=headers, json={**PRODUCT, **body})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.post(URL, json=PRODUCT)
    assert response.json() == {"code": 401, "message": "Unauthorized", "data": None}


async def test_create_and_duplicate(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    created = await favorite(
        client, headers, title=" <i>T</i> ", image="https://example.com/a.png", extra="{}"
    )
    assert created | {"id": 0, "createdAt": ""} == {
        "id": 0,
        "userId": 2,
        "targetType": "product",
        "targetId": "p1",
        "title": "T",
        "image": "https://example.com/a.png",
        "extra": "{}",
        "subjectId": None,
        "createdAt": "",
    }
    bare = await favorite(client, headers, targetType="article")
    assert (bare["title"], bare["image"], bare["extra"]) == (None, None, None)
    duplicate = await client.post(URL, headers={**headers, "Accept-Language": "zh"}, json=PRODUCT)
    assert duplicate.json() == {"code": 409, "message": "已收藏", "data": None}


async def test_unique_constraint_maps_to_already_favorited(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await auth(client)
    await favorite(client, headers)

    async def not_found(*_: object) -> None:
        return None

    monkeypatch.setattr(FavoritesService, "find_by_target", not_found)
    response = await client.post(URL, headers=headers, json=PRODUCT)
    assert response.json() == {"code": 409, "message": "Already in favorites", "data": None}


async def test_exam_targets(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    for target_id in ("q1", UUID):
        response = await client.post(
            URL, headers=headers, json={"targetType": "question", "targetId": target_id}
        )
        assert response.json() == {
            "code": 403,
            "message": "This question is not accessible",
            "data": {"errorKey": "questionNotAccessible"},
        }
    for target_id in ("p1", UUID):
        response = await client.post(
            URL + "/toggle", headers=headers, json={"targetType": "paper", "targetId": target_id}
        )
        assert response.json() == {"code": 404, "message": "Paper not found", "data": None}


async def test_validation(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    response = await client.post(URL + "/check", headers=headers, json={})
    assert response.json() == {
        "code": 422,
        "message": "Validation failed",
        "data": {
            "targetType": "targetType must be one of the following values: "
            "product, article, store, question, paper, targetType should not be empty",
            "targetId": "targetId must be shorter than or equal to 100 characters, "
            "targetId must be a string, targetId should not be empty",
        },
    }


async def test_check_and_toggle(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    missing = await client.post(URL + "/check", headers=headers, json=PRODUCT)
    assert missing.json()["data"] == {"isFavorited": False}
    toggled = (await client.post(URL + "/toggle", headers=headers, json=PRODUCT)).json()["data"]
    assert toggled["isFavorited"] is True
    assert toggled["favorite"]["targetId"] == "p1"
    found = await client.post(URL + "/check", headers=headers, json=PRODUCT)
    assert found.json()["data"] == {"isFavorited": True, "favoriteId": toggled["favorite"]["id"]}
    untoggled = await client.post(URL + "/toggle", headers=headers, json=PRODUCT)
    assert untoggled.json()["data"] == {"isFavorited": False}


async def test_list(client: httpx.AsyncClient, connection: AsyncConnection) -> None:
    headers = await auth(client)
    product = await favorite(client, headers)
    article = await favorite(client, headers, targetType="article", targetId="a1")
    await connection.execute(
        text(
            'INSERT INTO favorite ("userId", "targetType", "targetId", "createdAt") '
            "VALUES (2, 'question', :id, now() - interval '1 day')"
        ),
        {"id": UUID.lower()},
    )
    await connection.execute(
        text("UPDATE favorite SET \"createdAt\" = now() + interval '1 minute' WHERE id = :id"),
        {"id": article["id"]},
    )
    listed = (await client.get(URL, headers=headers)).json()["data"]
    assert [(f["targetType"], f["targetOffline"]) for f in listed["list"]] == [
        ("article", None),
        ("product", None),
        ("question", True),
    ]
    assert list(listed["list"][1]) == [*product, "targetOffline"]
    assert listed["total"] == 3
    filtered = await client.get(URL, headers=headers, params={"targetType": "product"})
    assert [f["id"] for f in filtered.json()["data"]["list"]] == [product["id"]]
    by_subject = await client.get(URL, headers=headers, params={"subjectId": UUID})
    assert by_subject.json()["data"]["total"] == 0
    capped = await client.get(URL, headers=headers, params={"limit": "100", "page": "2"})
    assert capped.json()["data"] | {"list": None} == {
        "list": None,
        "total": 3,
        "page": 2,
        "limit": 50,
        "totalPages": 1,
    }
    invalid = await client.get(URL, headers=headers, params={"subjectId": "x"})
    assert invalid.json()["data"] == {"subjectId": "subjectId must be a UUID"}


async def test_delete(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    created = await favorite(client, headers)
    other = await client.delete(
        f"{URL}/{created['id']}", headers=await auth(client, "admin@example.com")
    )
    assert other.json() == {"code": 404, "message": "Favorite not found", "data": None}
    deleted = await client.delete(f"{URL}/{created['id']}", headers=headers)
    assert deleted.json() == {"code": 200, "message": "success"}
    invalid = await client.delete(f"{URL}/check", headers=headers)
    assert invalid.json() == {"code": 400, "message": "Bad request", "data": {"id": "Invalid id"}}


async def test_account_cleaner_removes_favorites(
    app: FastAPI, client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    headers = await auth(client)
    await favorite(client, headers)
    await favorite(client, await auth(client, "admin@example.com"))
    await FavoritesAccountCleaner(app.state.context).clean(2)
    rows = await connection.execute(text('SELECT "userId" FROM favorite'))
    assert [row[0] for row in rows] == [1]
