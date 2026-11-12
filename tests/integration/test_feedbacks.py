from typing import Any

from sqlalchemy import text
lazy import httpx2 as httpx
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.feedbacks.account_cleaner import FeedbacksAccountCleaner
from tests.integration.test_addresses import auth

URL = "/api/v1/feedbacks"


async def submit(client: httpx.AsyncClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    response = await client.post(URL, headers=headers, json={"type": "bug", "content": "c", **body})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def insert_report(connection: AsyncConnection, type_: str) -> int:
    feedback_id = await connection.scalar(
        text(
            "INSERT INTO feedback (\"userId\", type, content) VALUES (2, :type, 'r') RETURNING id"
        ),
        {"type": type_},
    )
    return int(feedback_id)


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get(URL)
    assert response.json() == {"code": 401, "message": "Unauthorized", "data": None}


async def test_create(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    bare = await submit(client, headers)
    assert bare | {"id": 0, "createdAt": "", "updatedAt": ""} == {
        "id": 0,
        "userId": 2,
        "type": "bug",
        "content": "c",
        "contact": None,
        "status": "pending",
        "reply": None,
        "questionId": None,
        "questionVersion": None,
        "reason": None,
        "errataId": None,
        "messageId": None,
        "messageSnapshot": None,
        "createdAt": "",
        "updatedAt": "",
    }
    full = await submit(client, headers, type="suggestion", images=["a", "b"], contact="x")
    assert list(full)[:6] == ["id", "userId", "type", "content", "images", "contact"]
    assert (full["images"], full["contact"]) == (["a", "b"], "x")


async def test_create_validation(client: httpx.AsyncClient) -> None:
    headers = await auth(client)
    response = await client.post(
        URL,
        headers={**headers, "Accept-Language": "en"},
        json={"type": "question_error", "content": "", "images": [1, "a", "b", "c", "d", "e"]},
    )
    assert response.json() == {
        "code": 422,
        "message": "Validation failed",
        "data": {
            "type": "type must be one of the following values: bug, suggestion, complaint, other",
            "content": "content should not be empty",
            "images": "images must contain no more than 5 elements, "
            "each value in images must be shorter than or equal to 1000 characters, "
            "each value in images must be a string",
        },
    }


async def test_list(client: httpx.AsyncClient, connection: AsyncConnection) -> None:
    headers = await auth(client)
    first = await submit(client, headers)
    second = await submit(client, headers, type="other")
    third = await submit(client, headers, type="complaint")
    await insert_report(connection, "question_error")
    await insert_report(connection, "ai_content")
    listed = (await client.get(URL, headers=headers)).json()["data"]
    assert [f["id"] for f in listed["list"]] == [third["id"], second["id"], first["id"]]
    assert {k: listed[k] for k in ("total", "page", "limit", "totalPages")} == {
        "total": 3,
        "page": 1,
        "limit": 10,
        "totalPages": 1,
    }
    reports = await client.get(URL, headers=headers, params={"type": "question_error"})
    assert reports.json()["data"]["total"] == 1
    by_status = await client.get(URL, headers=headers, params={"status": "closed"})
    assert by_status.json()["data"]["total"] == 0
    fractional = await client.get(URL, headers=headers, params={"page": "2", "limit": "1.5"})
    assert fractional.json()["data"] | {"list": None} == {
        "list": None,
        "total": 2.5,
        "page": 2,
        "limit": 1.5,
        "totalPages": 2,
    }
    assert [f["id"] for f in fractional.json()["data"]["list"]] == [first["id"]]
    capped = await client.get(URL, headers=headers, params={"limit": "100"})
    assert capped.json()["data"]["limit"] == 50
    invalid = await client.get(URL, headers=headers, params={"type": "ai_content", "page": "0"})
    assert invalid.json()["data"] == {
        "type": "type must be one of the following values: "
        "bug, suggestion, complaint, other, question_error",
        "page": "page must not be less than 1",
    }


async def test_detail(client: httpx.AsyncClient, connection: AsyncConnection) -> None:
    headers = await auth(client)
    feedback = await submit(client, headers)
    assert (await client.get(f"{URL}/{feedback['id']}", headers=headers)).json()["data"] == feedback
    report = await insert_report(connection, "question_error")
    assert (await client.get(f"{URL}/{report}", headers=headers)).json()["data"]["id"] == report
    not_found = {"code": 404, "message": "Feedback not found", "data": None}
    hidden = await insert_report(connection, "ai_content")
    assert (await client.get(f"{URL}/{hidden}", headers=headers)).json() == not_found
    admin = await auth(client, "admin@example.com")
    assert (await client.get(f"{URL}/{feedback['id']}", headers=admin)).json() == not_found
    invalid = await client.get(f"{URL}/abc", headers=headers)
    assert invalid.json() == {"code": 400, "message": "Bad request", "data": {"id": "Invalid id"}}


async def test_account_cleaner_anonymizes_feedbacks(
    app: FastAPI, client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    headers = await auth(client)
    feedback = await submit(client, headers, images=["a"], contact="x")
    await FeedbacksAccountCleaner(app.state.context).clean(2)
    row = (
        await connection.execute(
            text('SELECT "userId", contact, images IS NULL, content FROM feedback WHERE id = :id'),
            {"id": feedback["id"]},
        )
    ).one()
    assert tuple(row) == (None, None, True, "c")
