import uuid

from sqlalchemy import text
lazy import httpx2 as httpx
lazy import pytest
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.users.account_cleaner import UsersAccountCleaner
from app.infrastructure.account_cleanup import AccountCleanupProducer, registered_cleaners
from tests.integration.test_auth import bearer, login

URL = "/api/v1/admin/users/{}/cleanup"


async def admin_headers(client: httpx.AsyncClient) -> dict[str, str]:
    return bearer((await login(client, "admin@example.com"))["token"])


class TestRetrigger:
    async def test_active_user_is_rejected(self, client: httpx.AsyncClient) -> None:
        response = await client.post(URL.format(2), headers=await admin_headers(client))
        assert response.json() == {
            "code": 422,
            "message": "Validation failed",
            "data": {"user": "User has not been deleted"},
        }

    async def test_unknown_user_is_not_found(self, client: httpx.AsyncClient) -> None:
        response = await client.post(URL.format(999_999), headers=await admin_headers(client))
        assert response.json() == {"code": 404, "message": "User not found", "data": None}

    async def test_requires_user_delete_permission(self, client: httpx.AsyncClient) -> None:
        token = (await login(client, "john.doe@example.com"))["token"]
        response = await client.post(URL.format(1), headers=bearer(token))
        assert response.status_code == 403

    async def test_deleted_user_is_enqueued(
        self,
        client: httpx.AsyncClient,
        connection: AsyncConnection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        enqueued: list[int] = []

        async def record(_: AccountCleanupProducer, user_id: int) -> None:
            enqueued.append(user_id)

        monkeypatch.setattr(AccountCleanupProducer, "enqueue_cleanup", record)
        headers = await admin_headers(client)
        await connection.execute(text('UPDATE "user" SET "deletedAt" = now() WHERE id = 2'))
        response = await client.post(URL.format(2), headers=headers)
        assert response.json() == {"code": 200, "message": "success"}
        assert enqueued == [2]


async def test_users_cleaner_detaches_photo(app: FastAPI, connection: AsyncConnection) -> None:
    photo_id = uuid.uuid4()
    await connection.execute(
        text("INSERT INTO file (id, path) VALUES (:id, '/files/avatar.png')"), {"id": photo_id}
    )
    await connection.execute(
        text(
            'UPDATE "user" SET "photoId" = :id, '
            "\"updatedAt\" = now() - interval '1 day' WHERE id = 2"
        ),
        {"id": photo_id},
    )
    await UsersAccountCleaner(app.state.context).clean(2)
    row = (
        await connection.execute(
            text(
                'SELECT "photoId", "updatedAt" > now() - interval \'1 hour\' '
                'FROM "user" WHERE id = 2'
            )
        )
    ).one()
    assert tuple(row) == (None, True)


def test_users_cleaner_runs_before_files_cleaner(app: FastAPI) -> None:
    names = [cleaner.name for cleaner in registered_cleaners(app.state.context)]
    assert names.index("users") < names.index("files")
