import asyncio
import json
import time
from typing import Any

import pytest
from sqlalchemy import text
lazy import httpx2 as httpx
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.infrastructure.account_cleanup import registered_cleaners
from app.infrastructure.database.session import Database
from app.infrastructure.queue import notification as notification_queue
from app.infrastructure.queue.notification import JobLimiter
from app.modules.notifications.account_cleaner import NotificationsAccountCleaner
from app.modules.notifications.repository import InAppBatch
from app.modules.notifications.service import CreateNotification, NotificationsService
from tests.integration.test_auth import bearer, login

URL = "/api/v1/notifications"
ISO = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z"


async def user_id(connection: AsyncConnection, email: str) -> int:
    return int(
        (
            await connection.execute(text('SELECT id FROM "user" WHERE email = :e'), {"e": email})
        ).scalar_one()
    )


async def insert(
    connection: AsyncConnection,
    owner: int,
    *,
    title: str = "Hello",
    category: str = "SYSTEM",
    channels: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    is_read: bool = False,
    deleted: bool = False,
    age_seconds: int = 0,
) -> int:
    result = await connection.execute(
        text(
            'INSERT INTO notification ("userId", type, category, title, content, metadata, '
            'channels, "isRead", "deletedAt", "createdAt") VALUES (:user, \'INFO\', :category, '
            ":title, 'Body', CAST(:metadata AS jsonb), CAST(:channels AS jsonb), :read, "
            "CASE WHEN :deleted THEN now() END, now() - make_interval(secs => :age)) "
            "RETURNING id"
        ),
        {
            "user": owner,
            "category": category,
            "title": title,
            "metadata": json.dumps(metadata) if metadata is not None else None,
            "channels": json.dumps(channels if channels is not None else ["IN_APP"]),
            "read": is_read,
            "deleted": deleted,
            "age": age_seconds,
        },
    )
    return int(result.scalar_one())


@pytest.fixture
async def john(client: httpx.AsyncClient) -> dict[str, str]:
    return bearer((await login(client, "john.doe@example.com"))["token"])


@pytest.fixture
async def john_id(connection: AsyncConnection) -> int:
    return await user_id(connection, "john.doe@example.com")


class TestList:
    async def test_lists_visible_notifications_newest_first(
        self,
        client: httpx.AsyncClient,
        connection: AsyncConnection,
        john: dict[str, str],
        john_id: int,
    ) -> None:
        older = await insert(connection, john_id, title="older", age_seconds=60)
        newer = await insert(
            connection, john_id, title="newer", metadata={"orderId": 1}, category="ORDER"
        )
        await insert(connection, john_id, title="email only", channels=["EMAIL"])
        await insert(connection, john_id, title="deleted", deleted=True)
        admin_id = await user_id(connection, "admin@example.com")
        await insert(connection, admin_id, title="someone else")

        response = await client.get(URL, headers=john)

        assert response.status_code == 200
        data = response.json()["data"]
        assert {k: data[k] for k in ("total", "page", "limit", "totalPages")} == {
            "total": 2,
            "page": 1,
            "limit": 10,
            "totalPages": 1,
        }
        assert [n["id"] for n in data["list"]] == [newer, older]
        first, second = data["list"]
        assert list(first) == [
            "id",
            "userId",
            "type",
            "category",
            "title",
            "content",
            "metadata",
            "channels",
            "sentChannels",
            "isRead",
            "createdAt",
            "updatedAt",
            "deletedAt",
        ]
        assert "metadata" not in second
        assert (first["metadata"], first["deletedAt"], first["sentChannels"]) == (
            {"orderId": 1},
            None,
            {},
        )

    async def test_filters_by_category_and_read_status(
        self,
        client: httpx.AsyncClient,
        connection: AsyncConnection,
        john: dict[str, str],
        john_id: int,
    ) -> None:
        order = await insert(connection, john_id, category="ORDER")
        read = await insert(connection, john_id, is_read=True)
        await insert(connection, john_id)

        by_category = await client.get(URL, params={"category": "ORDER"}, headers=john)
        by_read = await client.get(URL, params={"isRead": "true"}, headers=john)

        assert [n["id"] for n in by_category.json()["data"]["list"]] == [order]
        assert [n["id"] for n in by_read.json()["data"]["list"]] == [read]

    async def test_caps_limit_and_paginates(
        self,
        client: httpx.AsyncClient,
        connection: AsyncConnection,
        john: dict[str, str],
        john_id: int,
    ) -> None:
        for index in range(3):
            await insert(connection, john_id, age_seconds=index)

        capped = await client.get(URL, params={"limit": "500"}, headers=john)
        second_page = await client.get(URL, params={"page": "2", "limit": "2"}, headers=john)

        assert capped.json()["data"]["limit"] == 50
        assert {k: second_page.json()["data"][k] for k in ("total", "page", "totalPages")} == {
            "total": 3,
            "page": 2,
            "totalPages": 2,
        }
        assert len(second_page.json()["data"]["list"]) == 1

    async def test_rejects_invalid_query(
        self, client: httpx.AsyncClient, john: dict[str, str]
    ) -> None:
        response = await client.get(
            URL, params={"page": "-1", "category": "NOPE", "isRead": "x"}, headers=john
        )

        assert response.status_code == 422
        assert response.json() == {
            "code": 422,
            "message": "Validation failed",
            "data": {
                "page": "page must not be less than 1",
                "category": (
                    "category must be one of the following values: "
                    "ORDER, PAYMENT, SYSTEM, PROMOTION, ACTIVITY, STUDY"
                ),
                "isRead": "isRead must be a boolean value",
            },
        }

    async def test_requires_authentication(self, client: httpx.AsyncClient) -> None:
        response = await client.get(URL)

        assert response.json() == {"code": 401, "message": "Unauthorized", "data": None}


class TestSingle:
    async def test_unread_count_counts_visible_unread(
        self,
        client: httpx.AsyncClient,
        connection: AsyncConnection,
        john: dict[str, str],
        john_id: int,
    ) -> None:
        await insert(connection, john_id)
        await insert(connection, john_id, is_read=True)
        await insert(connection, john_id, channels=["EMAIL"])

        response = await client.get(f"{URL}/unread-count", headers=john)

        assert response.json() == {"code": 200, "message": "success", "data": 1}

    async def test_get_one_and_not_found(
        self,
        client: httpx.AsyncClient,
        connection: AsyncConnection,
        john: dict[str, str],
        john_id: int,
    ) -> None:
        own = await insert(connection, john_id)
        foreign = await insert(connection, await user_id(connection, "admin@example.com"))

        found = await client.get(f"{URL}/{own}", headers=john)
        missing = await client.get(f"{URL}/{foreign}", headers=john)
        invalid = await client.get(f"{URL}/abc", headers=john)

        assert found.json()["data"]["id"] == own
        assert missing.status_code == 404
        assert missing.json() == {"code": 404, "message": "Notification not found", "data": None}
        assert invalid.json() == {
            "code": 400,
            "message": "Bad request",
            "data": {"id": "Invalid id"},
        }

    async def test_mark_as_read_sets_read_at(
        self,
        client: httpx.AsyncClient,
        connection: AsyncConnection,
        john: dict[str, str],
        john_id: int,
    ) -> None:
        own = await insert(connection, john_id)

        response = await client.post(f"{URL}/{own}/read", headers=john)
        missing = await client.post(f"{URL}/999999/read", headers=john)

        data = response.json()["data"]
        assert data["isRead"] is True
        assert list(data)[9] == "readAt"
        assert data["readAt"].endswith("Z")
        assert missing.status_code == 404

    async def test_mark_all_as_read(
        self,
        client: httpx.AsyncClient,
        connection: AsyncConnection,
        john: dict[str, str],
        john_id: int,
    ) -> None:
        await insert(connection, john_id)
        await insert(connection, john_id)

        response = await client.post(f"{URL}/read-all", headers=john)
        count = await client.get(f"{URL}/unread-count", headers=john)

        assert response.json() == {"code": 200, "message": "success"}
        assert count.json()["data"] == 0

    async def test_delete_soft_deletes(
        self,
        client: httpx.AsyncClient,
        connection: AsyncConnection,
        john: dict[str, str],
        john_id: int,
    ) -> None:
        own = await insert(connection, john_id)

        deleted = await client.delete(f"{URL}/{own}", headers=john)
        again = await client.delete(f"{URL}/{own}", headers=john)

        assert deleted.json() == {"code": 200, "message": "success"}
        assert again.status_code == 404
        row = (
            await connection.execute(
                text('SELECT "deletedAt" IS NOT NULL FROM notification WHERE id = :id'),
                {"id": own},
            )
        ).scalar_one()
        assert row is True


class TestSettings:
    async def test_create_then_update_setting(
        self, client: httpx.AsyncClient, john: dict[str, str], john_id: int
    ) -> None:
        body = {
            "category": "ORDER",
            "enableInApp": True,
            "enableEmail": False,
            "enableSms": False,
            "enablePush": True,
        }

        created = await client.put(f"{URL}/settings", json=body, headers=john)
        unchanged = await client.put(f"{URL}/settings", json=body, headers=john)
        updated = await client.put(
            f"{URL}/settings", json={**body, "enableEmail": True}, headers=john
        )
        listed = await client.get(f"{URL}/settings", headers=john)

        data = created.json()["data"]
        assert list(data) == [
            "id",
            "userId",
            "category",
            "enableInApp",
            "enableEmail",
            "enableSms",
            "enablePush",
            "createdAt",
            "updatedAt",
        ]
        assert (data["userId"], data["enableEmail"]) == (john_id, False)
        assert unchanged.json()["data"] == data
        assert updated.json()["data"]["id"] == data["id"]
        assert updated.json()["data"]["enableEmail"] is True
        assert [s["category"] for s in listed.json()["data"]] == ["ORDER"]

    async def test_rejects_invalid_body(
        self, client: httpx.AsyncClient, john: dict[str, str]
    ) -> None:
        response = await client.put(f"{URL}/settings", json={"category": "X"}, headers=john)

        assert response.status_code == 422
        assert set(response.json()["data"]) == {
            "category",
            "enableInApp",
            "enableEmail",
            "enableSms",
            "enablePush",
        }


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, task: Any, payload: dict[str, Any]) -> None:
        self.jobs.append((task.task_name, payload))


class TestService:
    async def test_create_filters_channels_by_setting_and_queues_after_commit(
        self, connection: AsyncConnection, john_id: int
    ) -> None:
        await connection.execute(
            text(
                'INSERT INTO notification_setting ("userId", category, "enableSms") '
                "VALUES (:user, 'ORDER', false)"
            ),
            {"user": john_id},
        )
        queue = FakeQueue()
        database = Database.bound_to(connection)
        async with database.transaction() as session:
            notification = await NotificationsService(session, queue).create(  # type: ignore[arg-type]
                CreateNotification(
                    user_id=john_id,
                    type="INFO",
                    category="ORDER",
                    title="Shipped",
                    content="Your order shipped",
                    channels=["IN_APP", "EMAIL", "SMS"],
                )
            )
            assert queue.jobs == []

        assert notification.channels == ["IN_APP", "EMAIL"]
        assert notification.metadata_ is None
        assert queue.jobs == [
            (
                "notification:send-email",
                {
                    "notificationId": notification.id,
                    "userId": john_id,
                    "title": "Shipped",
                    "content": "Your order shipped",
                },
            )
        ]
        stored = (
            await connection.execute(
                text("SELECT metadata IS NULL FROM notification WHERE id = :id"),
                {"id": notification.id},
            )
        ).scalar_one()
        assert stored is True

    async def test_create_queues_sms_and_wechat_subscribe_jobs(
        self, connection: AsyncConnection, john_id: int
    ) -> None:
        subscribe = {"scene": "orderPaid", "values": {"orderNo": "NO1"}, "page": "pages/x"}
        queue = FakeQueue()
        async with Database.bound_to(connection).transaction() as session:
            service = NotificationsService(session, queue)  # type: ignore[arg-type]
            with_subscribe = await service.create(
                CreateNotification(
                    user_id=john_id,
                    type="SUCCESS",
                    category="PAYMENT",
                    title="支付成功",
                    content="订单 NO1 已支付",
                    channels=["IN_APP", "SMS", "PUSH"],
                    metadata={"orderNo": "NO1", "wechatSubscribe": subscribe},
                )
            )
            without_subscribe = await service.create(
                CreateNotification(
                    user_id=john_id,
                    type="INFO",
                    category="SYSTEM",
                    title="Hi",
                    content="Body",
                    channels=["PUSH"],
                )
            )

        assert without_subscribe.channels == ["PUSH"]
        assert queue.jobs == [
            (
                "notification:send-sms",
                {
                    "notificationId": with_subscribe.id,
                    "userId": john_id,
                    "content": "订单 NO1 已支付",
                },
            ),
            (
                "notification:send-wechat-subscribe",
                {"notificationId": with_subscribe.id, "userId": john_id, "payload": subscribe},
            ),
        ]

    async def test_create_in_app_batch_skips_disabled_users(
        self, connection: AsyncConnection, john_id: int
    ) -> None:
        admin_id = await user_id(connection, "admin@example.com")
        await connection.execute(
            text(
                'INSERT INTO notification_setting ("userId", category, "enableInApp") '
                "VALUES (:user, 'SYSTEM', false)"
            ),
            {"user": admin_id},
        )
        async with Database.bound_to(connection).transaction() as session:
            written = await NotificationsService(session).create_in_app_batch(
                InAppBatch(
                    user_ids=[john_id, admin_id],
                    type="INFO",
                    category="SYSTEM",
                    title="Batch",
                    content="B",
                    metadata=None,
                )
            )

        assert written == 1


async def test_account_cleaner_removes_notifications_and_settings(
    app: FastAPI, connection: AsyncConnection, john_id: int
) -> None:
    await insert(connection, john_id)
    await insert(connection, john_id, deleted=True)
    await connection.execute(
        text("INSERT INTO notification_setting (\"userId\", category) VALUES (:u, 'ORDER')"),
        {"u": john_id},
    )

    await NotificationsAccountCleaner(app.state.context).clean(john_id)

    counts = (
        await connection.execute(
            text(
                'SELECT (SELECT count(*) FROM notification WHERE "userId" = :u), '
                '(SELECT count(*) FROM notification_setting WHERE "userId" = :u)'
            ),
            {"u": john_id},
        )
    ).one()
    assert tuple(counts) == (0, 0)
    names = [cleaner.name for cleaner in registered_cleaners(app.state.context)]
    assert names.index("notifications") < names.index("users")


class TestJobLimiter:
    async def test_limits_concurrency(self) -> None:
        limiter = JobLimiter(concurrency=2, max_jobs=100, duration_ms=1000)
        running = 0
        peak = 0

        async def job() -> None:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1

        await asyncio.gather(*(limiter.run(job) for _ in range(6)))

        assert peak == 2

    async def test_limits_rate_per_window(self) -> None:
        limiter = JobLimiter(concurrency=10, max_jobs=3, duration_ms=200)
        started: list[float] = []

        async def job() -> None:
            started.append(time.monotonic())

        await asyncio.gather(*(limiter.run(job) for _ in range(5)))

        assert started[3] - started[0] >= 0.19

    def test_notification_queue_uses_concurrency_5_and_10_per_second(self) -> None:
        limiter = notification_queue.limiter
        assert (limiter.concurrency, limiter.max_jobs, limiter.duration) == (5, 10, 1.0)
