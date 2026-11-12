from sqlalchemy import text
lazy import httpx2 as httpx
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.test_auth import bearer, login

URL = "/api/v1/app-profile"
ADMIN_URL = "/api/v1/admin/app-profile"
EMPTY = {
    "appName": None,
    "slogan": None,
    "companyName": None,
    "copyright": None,
    "icpLicense": None,
    "servicePhone": None,
    "serviceHours": None,
    "serviceEmail": None,
    "shareTitle": None,
}


async def admin(client: httpx.AsyncClient) -> dict[str, str]:
    return bearer((await login(client, "admin@example.com"))["token"])


async def test_unconfigured_profile_is_all_null(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    await connection.execute(text("DELETE FROM config WHERE key = 'app.profile'"))

    response = await client.get(URL)

    assert response.json() == {"code": 200, "message": "success", "data": EMPTY}
    assert list(response.json()["data"]) == list(EMPTY)


async def test_update_replaces_whole_profile(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    headers = await admin(client)

    first = await client.put(
        ADMIN_URL,
        json={"appName": "  备考  ", "slogan": "   ", "shareTitle": None},
        headers=headers,
    )
    second = await client.put(ADMIN_URL, json={"servicePhone": "400-123 4567"}, headers=headers)
    read = await client.get(URL)

    assert first.json()["data"] == {**EMPTY, "appName": "备考"}
    assert second.json()["data"] == {**EMPTY, "servicePhone": "400-123 4567"}
    assert read.json()["data"] == second.json()["data"]
    stored = (
        await connection.execute(
            text("SELECT value, \"isPublic\" FROM config WHERE key = 'app.profile'")
        )
    ).one()
    assert tuple(stored) == ({"value": second.json()["data"]}, False)


async def test_ignores_non_string_stored_values(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    await connection.execute(
        text(
            "INSERT INTO config (key, value, \"isPublic\") VALUES ('app.profile', "
            '\'{"value": {"appName": 1, "slogan": " 宣传语 ", "extra": "x"}}\', false) '
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
    )

    response = await client.get(URL)

    assert response.json()["data"] == {**EMPTY, "slogan": "宣传语"}


async def test_rejects_invalid_fields(client: httpx.AsyncClient) -> None:
    response = await client.put(
        ADMIN_URL,
        json={"servicePhone": "call me", "serviceEmail": "bad", "appName": "x" * 21},
        headers=await admin(client),
    )

    assert response.status_code == 422
    assert set(response.json()["data"]) == {"servicePhone", "serviceEmail", "appName"}


async def test_update_requires_permission(client: httpx.AsyncClient) -> None:
    john = bearer((await login(client, "john.doe@example.com"))["token"])

    forbidden = await client.put(ADMIN_URL, json={}, headers=john)
    anonymous = await client.put(ADMIN_URL, json={})

    assert (forbidden.status_code, anonymous.status_code) == (403, 401)
