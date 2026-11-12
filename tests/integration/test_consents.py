from sqlalchemy import text
lazy import httpx2 as httpx
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.test_auth import bearer, login

URL = "/api/v1/me/consents"
TYPES = ("PRIVACY_POLICY", "USER_AGREEMENT", "AI_SERVICE")


async def set_version(connection: AsyncConnection, key: str, version: str) -> None:
    await connection.execute(
        text(
            'INSERT INTO config (key, value, "isPublic") '
            "VALUES (:key, CAST(:value AS jsonb), true) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"key": key, "value": f'{{"value": "{version}"}}'},
    )


async def john(client: httpx.AsyncClient) -> dict[str, str]:
    return bearer((await login(client, "john.doe@example.com"))["token"])


async def test_status_without_configured_versions(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    await connection.execute(text("DELETE FROM config WHERE key LIKE 'consent.%'"))

    response = await client.get(URL, headers=await john(client))

    assert response.json()["data"] == [
        {
            "type": kind,
            "currentVersion": None,
            "agreedVersion": None,
            "agreedAt": None,
            "agreed": False,
            "needsConsent": False,
        }
        for kind in TYPES
    ]


async def test_agree_current_version_and_upgrade(
    client: httpx.AsyncClient, connection: AsyncConnection, app: FastAPI
) -> None:
    await connection.execute(text("DELETE FROM config WHERE key LIKE 'consent.%'"))
    await set_version(connection, "consent.privacyPolicyVersion", "v1")
    headers = await john(client)

    agreed = await client.post(
        URL, json={"type": "PRIVACY_POLICY", "version": "v1"}, headers=headers
    )
    again = await client.post(
        URL, json={"type": "PRIVACY_POLICY", "version": "v1"}, headers=headers
    )
    status = await client.get(URL, headers=headers)

    data = agreed.json()["data"]
    assert list(data) == [
        "type",
        "currentVersion",
        "agreedVersion",
        "agreedAt",
        "agreed",
        "needsConsent",
    ]
    assert (data["agreedVersion"], data["agreed"], data["needsConsent"]) == ("v1", True, False)
    assert again.json()["data"]["agreedAt"] == data["agreedAt"]
    assert status.json()["data"][0] == data
    assert status.json()["data"][1]["needsConsent"] is False

    await set_version(connection, "consent.privacyPolicyVersion", "v2")
    await app.state.context.cache.set("config:version:consent.privacyPolicyVersion", "v2")
    upgraded = await client.get(URL, headers=headers)
    first = upgraded.json()["data"][0]
    assert (first["currentVersion"], first["agreedVersion"], first["needsConsent"]) == (
        "v2",
        "v1",
        True,
    )


async def test_rejects_outdated_version(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    await set_version(connection, "consent.aiServiceVersion", "v3")

    response = await client.post(
        URL, json={"type": "AI_SERVICE", "version": "v2"}, headers=await john(client)
    )

    assert response.json() == {
        "code": 422,
        "message": "Validation failed",
        "data": {"version": "The agreement version is outdated, please review and agree again"},
    }


async def test_rejects_invalid_body(client: httpx.AsyncClient) -> None:
    response = await client.post(URL, json={"type": "X"}, headers=await john(client))

    assert response.json()["data"] == {
        "type": "type must be one of the following values: " + ", ".join(TYPES),
        "version": (
            "version must be shorter than or equal to 50 characters, "
            "version should not be empty, version must be a string"
        ),
    }


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get(URL)).status_code == 401
