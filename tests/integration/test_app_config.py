from sqlalchemy import text
lazy import httpx2 as httpx
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from tests.integration.test_auth import bearer, login

URL = "/api/v1/config"


async def admin(client: httpx.AsyncClient) -> dict[str, str]:
    return bearer((await login(client, "admin@example.com"))["token"])


class TestGetConfig:
    async def test_public_registered_key(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"{URL}/ai.enabled")

        data = response.json()["data"]
        assert list(data) == ["key", "value", "isPublic", "createdAt", "updatedAt"]
        assert (data["key"], data["value"], data["isPublic"]) == (
            "ai.enabled",
            {"value": True},
            True,
        )

    async def test_registered_default_without_row(
        self, client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        await connection.execute(text("DELETE FROM config WHERE key = 'practice.trialSize'"))

        response = await client.get(f"{URL}/practice.trialSize")

        assert response.json()["data"] == {
            "key": "practice.trialSize",
            "value": {"value": 3},
            "isPublic": True,
            "createdAt": None,
            "updatedAt": None,
        }

    async def test_registry_overrides_stored_is_public(
        self, client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        await connection.execute(
            text(
                'INSERT INTO config (key, value, "isPublic") VALUES '
                "('ai.model', '{\"value\": \"m\"}', true), "
                "('custom.private', '{\"a\": 1}', false), "
                "('custom.public', '{\"a\": 1}', true)"
            )
        )

        private_registered = await client.get(f"{URL}/ai.model")
        private_custom = await client.get(f"{URL}/custom.private")
        public_custom = await client.get(f"{URL}/custom.public")

        assert private_registered.json() == {
            "code": 404,
            "message": "Config not found",
            "data": None,
        }
        assert private_custom.status_code == 404
        assert public_custom.json()["data"]["value"] == {"a": 1}

    async def test_unknown_key_is_not_found(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"{URL}/nope", headers={"Accept-Language": "zh"})

        assert response.json() == {"code": 404, "message": "配置不存在", "data": None}


class TestAdmin:
    async def test_admin_reads_private_key(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"{URL}/admin/ai.ratePerMinute", headers=await admin(client))

        assert response.json()["data"]["isPublic"] is False

    async def test_requires_permission(self, client: httpx.AsyncClient) -> None:
        john = bearer((await login(client, "john.doe@example.com"))["token"])

        read = await client.get(f"{URL}/admin/ai.enabled", headers=john)
        write = await client.put(f"{URL}/custom.key", json={"value": {}}, headers=john)
        anonymous = await client.put(f"{URL}/custom.key", json={"value": {}})

        assert (read.status_code, write.status_code, anonymous.status_code) == (403, 403, 401)

    async def test_upsert_creates_then_updates_and_invalidates_cache(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        headers = await admin(client)

        assert (await client.get(f"{URL}/custom.theme")).status_code == 404
        created = await client.put(
            f"{URL}/custom.theme",
            json={"value": {"b": 1, "a": 2}, "isPublic": True},
            headers=headers,
        )
        first_read = await client.get(f"{URL}/custom.theme")
        updated = await client.put(f"{URL}/custom.theme", json={"value": {"b": 2}}, headers=headers)
        second_read = await client.get(f"{URL}/custom.theme")

        assert created.json()["data"]["value"] == {"b": 1, "a": 2}
        assert list(created.json()["data"]["value"]) == ["b", "a"]
        assert created.json()["data"]["isPublic"] is True
        assert first_read.json()["data"]["value"] == {"b": 1, "a": 2}
        assert updated.json()["data"]["isPublic"] is True
        assert second_read.json()["data"]["value"] == {"b": 2}
        assert await app.state.context.cache.get("config:version:custom.theme") is not None

    async def test_upsert_uses_registry_is_public(self, client: httpx.AsyncClient) -> None:
        response = await client.put(
            f"{URL}/ai.ratePerMinute",
            json={"value": {"value": 20}, "isPublic": True},
            headers=await admin(client),
        )

        assert response.json()["data"]["isPublic"] is False

    async def test_rejects_dedicated_key(self, client: httpx.AsyncClient) -> None:
        response = await client.put(
            f"{URL}/app.profile", json={"value": {}}, headers=await admin(client)
        )

        assert response.json() == {
            "code": 422,
            "message": "Validation failed",
            "data": {
                "key": "This config can only be updated through its dedicated endpoint",
            },
        }

    async def test_rejects_invalid_body(self, client: httpx.AsyncClient) -> None:
        response = await client.put(
            f"{URL}/custom.key", json={"value": "x", "isPublic": 1}, headers=await admin(client)
        )

        assert response.json() == {
            "code": 422,
            "message": "Validation failed",
            "data": {
                "value": "value must be an object",
                "isPublic": "isPublic must be a boolean value",
            },
        }
