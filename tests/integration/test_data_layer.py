import asyncio
import logging
import uuid

import pytest
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
lazy import httpx2 as httpx
lazy from fastapi import FastAPI
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.errors import AppError
from app.identity.sessions.repository import SessionRepository
from app.identity.users.repository import UserRepository
from app.infrastructure.database.errors import find_unique_violation
from app.infrastructure.database.session import Database, after_commit
from tests.integration.conftest import (
    PgServer,
    _build_app,
    client_for,
    clone_database_async,
    error_of,
)
from tests.integration.identity import ADMIN_EMAIL, PASSWORD, USER_EMAIL, bearer, login


async def password_of(connection: AsyncConnection, email: str) -> str:
    row = await connection.execute(
        text("SELECT password_hash FROM users WHERE email = :email"), {"email": email}
    )
    return str(row.scalar_one())


async def test_unique_violation_exposes_constraint_name(connection: AsyncConnection) -> None:
    with pytest.raises(IntegrityError) as caught:
        async with connection.begin_nested():
            await connection.execute(
                text("INSERT INTO users (email) VALUES (:email)"), {"email": ADMIN_EMAIL}
            )
    violation = find_unique_violation(caught.value)
    assert violation is not None
    assert violation.constraint == "uq_users_email"


async def test_refresh_rejects_disabled_user(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    tokens = await login(client, USER_EMAIL)
    await connection.execute(
        text("UPDATE users SET status = 'disabled' WHERE email = :email"), {"email": USER_EMAIL}
    )
    response = await client.post(
        "/api/v1/auth/refresh", json={"refreshToken": tokens["refreshToken"]}
    )
    assert error_of(response) == (401, "auth.refreshTokenInvalid")


async def test_login_upgrades_hash_with_other_parameters(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    legacy = Argon2Hasher(memory_cost=65_536, time_cost=3, parallelism=4).hash(PASSWORD)
    await connection.execute(
        text("UPDATE users SET password_hash = :hash WHERE email = :email"),
        {"hash": legacy, "email": USER_EMAIL},
    )
    await login(client, USER_EMAIL)
    assert "$m=19456,t=2,p=1$" in await password_of(connection, USER_EMAIL)


async def test_rehash_keeps_concurrently_changed_password(
    app: FastAPI, connection: AsyncConnection
) -> None:
    stale = await password_of(connection, USER_EMAIL)
    changed = await app.state.context.passwords.hash_async("Changed00")
    async with app.state.context.database.transaction() as session:
        repository = UserRepository(session)
        user = await repository.find_by_email(USER_EMAIL)
        assert user is not None
        await connection.execute(
            text("UPDATE users SET password_hash = :hash WHERE id = :id"),
            {"hash": changed, "id": user.id},
        )
        await repository.rehash_password(user, stale, "rehashed")
    assert await password_of(connection, USER_EMAIL) == changed


async def test_touch_failure_does_not_fail_request(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    tokens = await login(client, USER_EMAIL)

    async def broken(*_: object) -> None:
        raise RuntimeError("touch failed")

    monkeypatch.setattr(SessionRepository, "touch", broken)
    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = await client.get("/api/v1/me", headers=bearer(tokens["accessToken"]))
    assert response.status_code == 200
    assert "Failed to update lastActiveAt" in caplog.text


class TestAfterCommit:
    async def test_http_error_commits_without_callbacks(self, connection: AsyncConnection) -> None:
        calls: list[str] = []

        async def record() -> None:
            calls.append("called")

        async def fail() -> None:
            async with Database.bound_to(connection).transaction() as session:
                after_commit(session, record)
                raise AppError("common.notFound")

        with pytest.raises(AppError):
            await fail()
        assert calls == []

    async def test_callback_failure_is_logged(
        self, connection: AsyncConnection, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def broken() -> None:
            raise RuntimeError("boom")

        database = Database.bound_to(connection)
        with caplog.at_level(logging.ERROR, logger="app.database"):
            async with database.transaction() as session:
                after_commit(session, broken)
        assert "after-commit callback failed" in caplog.text


async def test_user_list_orders_by_id_after_requested_sort(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    await connection.execute(
        text(
            "INSERT INTO users (email, display_name) "
            "SELECT 'order' || n || '@example.com', 'Same' FROM generate_series(1, 3) n"
        )
    )
    token = (await login(client, ADMIN_EMAIL))["accessToken"]
    for query in ("", "&sort=createdAt", "&sort=-createdAt"):
        response = await client.get(f"/api/v1/users?pageSize=10{query}", headers=bearer(token))
        users = response.json()["data"]["items"]
        same = [user["id"] for user in users if user["displayName"] == "Same"]
        assert len(same) == 3
        assert same == sorted(same)


async def test_authenticated_request_needs_one_connection(
    pg_server: PgServer, template_db: str
) -> None:
    name = await clone_database_async(pg_server, f"single_{uuid.uuid4().hex[:12]}")
    database = Database.from_settings(pg_server.settings(name), pool_size=1)
    try:
        async with client_for(_build_app(database)) as http:
            tokens = await login(http, USER_EMAIL)
            async with asyncio.timeout(10):
                response = await http.get("/api/v1/me", headers=bearer(tokens["accessToken"]))
        assert response.status_code == 200
    finally:
        await database.dispose()
