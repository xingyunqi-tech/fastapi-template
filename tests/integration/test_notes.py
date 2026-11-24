from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from app.infrastructure.database.session import Database
from app.modules.notes.account_cleaner import NotesAccountCleaner
from app.modules.notes.models import NoteModel
from tests.integration.conftest import error_of, rules_of
from tests.integration.identity import ADMIN_EMAIL, auth_headers

if TYPE_CHECKING:
    import httpx2 as httpx
    from sqlalchemy.ext.asyncio import AsyncConnection

URL = "/api/v1/notes"


async def create(client: httpx.AsyncClient, headers: dict[str, str], **body: str) -> dict[str, Any]:
    response = await client.post(URL, json={"title": "t", **body}, headers=headers)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


class TestNotes:
    async def test_crud(self, client: httpx.AsyncClient) -> None:
        headers = await auth_headers(client)
        created = await create(client, headers, title=" Groceries ", content="milk")
        assert (created["title"], created["content"], created["status"]) == (
            "Groceries",
            "milk",
            "draft",
        )
        item = f"{URL}/{created['id']}"
        assert (await client.get(item, headers=headers)).json()["data"] == created

        updated = await client.patch(item, json={"status": "published"}, headers=headers)
        assert updated.json()["data"]["status"] == "published"
        assert updated.json()["data"]["content"] == "milk"

        deleted = await client.delete(item, headers=headers)
        assert deleted.json() == {"code": 0, "message": "ok", "data": None}
        assert error_of(await client.get(item, headers=headers)) == (404, "common.notFound")

    async def test_list_filters_sorts_and_pages(self, client: httpx.AsyncClient) -> None:
        headers = await auth_headers(client)
        for title, status in (("b", "draft"), ("a", "published"), ("c", "published")):
            await create(client, headers, title=title, status=status)
        response = await client.get(
            URL,
            params={"status": "published", "sort": "title", "pageSize": 1},
            headers=headers,
        )
        page = response.json()["data"]
        assert [note["title"] for note in page["items"]] == ["a"]
        assert (page["total"], page["page"], page["pageSize"]) == (2, 1, 1)

    async def test_notes_of_other_users_are_not_found(self, client: httpx.AsyncClient) -> None:
        mine = await create(client, await auth_headers(client))
        other = await auth_headers(client, ADMIN_EMAIL)
        item = f"{URL}/{mine['id']}"
        for method in ("GET", "PATCH", "DELETE"):
            response = await client.request(method, item, json={}, headers=other)
            assert error_of(response) == (404, "common.notFound")
        assert (await client.get(URL, headers=other)).json()["data"]["total"] == 0

    async def test_validation(self, client: httpx.AsyncClient) -> None:
        headers = await auth_headers(client)
        response = await client.post(
            URL, json={"title": "", "content": "x" * 10_001, "status": "x"}, headers=headers
        )
        assert rules_of(response) == {
            "title": "minLength",
            "content": "maxLength",
            "status": "enum",
        }
        assert rules_of(await client.get(URL, params={"sort": "owner"}, headers=headers)) == {
            "query.sort": "enum"
        }

    async def test_requires_authentication(self, client: httpx.AsyncClient) -> None:
        assert error_of(await client.get(URL)) == (401, "common.unauthorized")


async def test_cleaner_deletes_notes_of_the_user(connection: AsyncConnection) -> None:
    owner, other = UUID(int=1), UUID(int=2)
    database = Database.bound_to(connection)
    async with database.transaction() as session:
        session.add_all(
            [NoteModel(owner_id=str(owner), title="a"), NoteModel(owner_id=str(other), title="b")]
        )
    await NotesAccountCleaner(database).clean(owner)
    await NotesAccountCleaner(database).clean(owner)
    async with database.transaction() as session:
        owners = list(await session.scalars(select(NoteModel.owner_id)))
    assert owners == [str(other)]
