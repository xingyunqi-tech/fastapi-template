import hashlib
from typing import Any

from sqlalchemy import text
lazy import httpx2 as httpx
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.infrastructure.account_cleanup import registered_cleaners
from app.modules.addresses.account_cleaner import AddressesAccountCleaner

URL = "/api/v1/addresses"
PASSWORD = hashlib.md5(b"Secret00", usedforsecurity=False).hexdigest()
BEIJING = {
    "provinceCode": "110000000000",
    "cityCode": "110100000000",
    "districtCode": "110101000000",
}
NAMED = {"name": "Ann", "phone": "13800138000", "province": "P", "city": "C", "district": "D"}


async def auth(client: httpx.AsyncClient, email: str = "john.doe@example.com") -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/email/login", json={"email": email, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['data']['token']}"}


async def create(client: httpx.AsyncClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    response = await client.post(URL, headers=headers, json={**NAMED, "address": "a", **body})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get(URL)
    assert response.json() == {"code": 401, "message": "Unauthorized", "data": None}


async def test_first_address_becomes_default(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    response = await client.post(
        URL, headers=headers, json={**NAMED, "name": " <b>Ann</b> ", "address": "road"}
    )
    body = response.json()
    assert response.status_code == 200
    assert list(body["data"]) == [
        "id",
        "userId",
        "name",
        "phone",
        "provinceCode",
        "cityCode",
        "districtCode",
        "province",
        "city",
        "district",
        "address",
        "isDefault",
        "createdAt",
        "updatedAt",
        "deletedAt",
    ]
    assert body["data"] | {"id": 0, "createdAt": "", "updatedAt": ""} == {
        "id": 0,
        "userId": 2,
        "name": "Ann",
        "phone": "13800138000",
        "provinceCode": None,
        "cityCode": None,
        "districtCode": None,
        "province": "P",
        "city": "C",
        "district": "D",
        "address": "road",
        "isDefault": True,
        "createdAt": "",
        "updatedAt": "",
        "deletedAt": None,
    }
    second = await create(client, headers)
    assert second["isDefault"] is False


async def test_region_codes_fill_names(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    response = await client.post(
        URL,
        headers=headers,
        json={"name": "B", "phone": "13800138000", **BEIJING, "address": "x", "isDefault": False},
    )
    data = response.json()["data"]
    assert (data["province"], data["city"], data["district"]) == ("北京市", "市辖区", "东城区")
    assert data["isDefault"] is False


async def test_region_code_errors(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    invalid = await client.post(
        URL,
        headers=headers,
        json={**NAMED, **BEIJING, "cityCode": "120100000000", "address": "x"},
    )
    assert invalid.json() == {
        "code": 400,
        "message": "Invalid province/city/district combination",
        "data": None,
    }
    address = await create(client, headers)
    incomplete = await client.put(
        f"{URL}/{address['id']}", headers=headers, json={"cityCode": "110100000000"}
    )
    assert incomplete.json() == {
        "code": 400,
        "message": "Must provide all three region codes (province, city, district) or none at all",
        "data": None,
    }


async def test_region_codes_resolve_through_region_cache(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    cache = app.state.context.cache
    await cache.set(
        "region:code:110101000000",
        {"code": "110101000000", "name": "缓存区", "level": 3, "parentCode": "110100000000"},
        60_000,
    )
    headers = await auth(client)
    created = await create(client, headers, **BEIJING)
    assert (created["province"], created["city"], created["district"]) == (
        "北京市",
        "市辖区",
        "缓存区",
    )
    assert (await cache.get("region:code:110000000000"))["name"] == "北京市"

    missing = {**BEIJING, "districtCode": "110199999999"}
    response = await client.post(URL, headers=headers, json={**NAMED, **missing, "address": "x"})
    assert response.json()["message"] == "Invalid province/city/district combination"
    assert await cache.get("region:code-miss:110199999999") is True


async def test_create_validation(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    response = await client.post(
        URL, headers=headers, json={**NAMED, "phone": "1", "address": "x", "province": None}
    )
    assert response.status_code == 422
    assert response.json()["data"] == {
        "phone": "phone must be a valid Chinese mobile number",
        "province": "province must be shorter than or equal to 50 characters, "
        "province must be a string, province is required when region codes are not provided",
    }


async def test_limit_per_user(client: httpx.AsyncClient, connection: AsyncConnection) -> None:
    headers = await auth(client)
    await connection.execute(
        text(
            'INSERT INTO address ("userId", name, phone, province, city, district, address) '
            "SELECT 2, 'n', '13800138000', 'p', 'c', 'd', 'a' FROM generate_series(1, 20)"
        )
    )
    response = await client.post(URL, headers=headers, json={**NAMED, "address": "x"})
    assert response.json() == {
        "code": 403,
        "message": "Maximum 20 addresses allowed per user",
        "data": None,
    }


async def test_list_filters_sort_and_pagination(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    first = await create(client, headers, name="A")
    second = await create(client, headers, name="B", **BEIJING)
    listed = (await client.get(URL, headers=headers)).json()["data"]
    assert [a["id"] for a in listed["list"]] == [first["id"], second["id"]]
    assert {k: listed[k] for k in ("total", "page", "limit", "totalPages")} == {
        "total": 2,
        "page": 1,
        "limit": 10,
        "totalPages": 1,
    }
    by_code = await client.get(
        URL, headers=headers, params={"filters": '{"provinceCode":"110000000000"}'}
    )
    assert [a["id"] for a in by_code.json()["data"]["list"]] == [second["id"]]
    sorted_page = await client.get(
        URL,
        headers=headers,
        params={"sort": '[{"orderBy":"name","order":"desc"}]', "limit": "1", "page": "2"},
    )
    assert [a["id"] for a in sorted_page.json()["data"]["list"]] == [first["id"]]
    capped = await client.get(URL, headers=headers, params={"limit": "60"})
    assert capped.json()["data"]["limit"] == 50
    invalid = await client.get(URL, headers=headers, params={"page": "0", "sort": "{}"})
    assert invalid.json()["data"] == {
        "page": "page must not be less than 1",
        "sort": {
            "orderBy": "orderBy must be one of the following values: "
            "id, name, isDefault, createdAt, updatedAt",
            "order": "order must be one of the following values: ASC, DESC",
        },
    }


async def test_get_default_and_detail(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    empty = await client.get(f"{URL}/default", headers=headers)
    assert empty.json() == {"code": 200, "message": "success", "data": None}
    address = await create(client, headers)
    assert (await client.get(f"{URL}/default", headers=headers)).json()["data"] == address
    assert (await client.get(f"{URL}/{address['id']}", headers=headers)).json()["data"] == address
    other = await client.get(
        f"{URL}/{address['id']}", headers=await auth(client, "admin@example.com")
    )
    assert other.json() == {"code": 404, "message": "Address not found", "data": None}
    invalid = await client.get(f"{URL}/abc", headers=headers)
    assert invalid.json() == {"code": 400, "message": "Bad request", "data": {"id": "Invalid id"}}


async def test_update_and_set_default(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    first = await create(client, headers)
    second = await create(client, headers)
    updated = await client.put(
        f"{URL}/{second['id']}", headers=headers, json={"name": "New", "isDefault": True}
    )
    assert updated.json()["data"]["name"] == "New"
    assert updated.json()["data"]["isDefault"] is True
    refreshed_first = await client.get(f"{URL}/{first['id']}", headers=headers)
    assert refreshed_first.json()["data"]["isDefault"] is False
    coded = await client.put(f"{URL}/{first['id']}", headers=headers, json=BEIJING)
    assert coded.json()["data"]["district"] == "东城区"
    reset = await client.put(f"{URL}/{second['id']}", headers=headers, json={"isDefault": False})
    assert reset.json()["data"]["isDefault"] is False
    default = await client.put(f"{URL}/{first['id']}/default", headers=headers)
    assert default.json()["data"]["isDefault"] is True
    assert default.json()["data"]["updatedAt"] == coded.json()["data"]["updatedAt"]
    missing = await client.put(f"{URL}/999999/default", headers=headers)
    assert missing.json() == {"code": 404, "message": "Address not found", "data": None}
    missing_update = await client.put(f"{URL}/999999", headers=headers, json={})
    assert missing_update.status_code == 404


async def test_delete_promotes_latest_address(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    headers = await auth(client)
    first = await create(client, headers)
    await create(client, headers, name="older")
    latest = await create(client, headers, name="latest")
    await connection.execute(
        text("UPDATE address SET \"createdAt\" = now() + interval '1 minute' WHERE id = :id"),
        {"id": latest["id"]},
    )
    deleted = await client.delete(f"{URL}/{first['id']}", headers=headers)
    assert deleted.json() == {"code": 200, "message": "success"}
    default = (await client.get(f"{URL}/default", headers=headers)).json()["data"]
    assert default["id"] == latest["id"]
    again = await client.delete(f"{URL}/{first['id']}", headers=headers)
    assert again.json() == {"code": 404, "message": "Address not found", "data": None}
    assert (await client.get(URL, headers=headers)).json()["data"]["total"] == 2


async def test_account_cleaner_removes_addresses(
    app: FastAPI, client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    headers = await auth(client)
    first = await create(client, headers)
    await client.delete(f"{URL}/{first['id']}", headers=headers)
    await create(client, headers)
    await AddressesAccountCleaner(app.state.context).clean(2)
    remaining = await connection.scalar(text('SELECT count(*) FROM address WHERE "userId" = 2'))
    assert remaining == 0


def test_account_cleaners_follow_nestjs_order(app: FastAPI) -> None:
    names = [cleaner.name for cleaner in registered_cleaners(app.state.context)]
    ordered = [name for name in names if name in {"favorites", "feedbacks", "addresses", "users"}]
    assert ordered == ["favorites", "feedbacks", "addresses", "users"]
