import asyncio
import hashlib
import logging
import uuid

import pytest
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession
lazy import httpx2 as httpx
lazy from fastapi import FastAPI

from app.common.exceptions import HttpError, find_unique_violation, not_found
from app.config import Settings
from app.core.auth.router import _service
from app.core.session.repository import SessionRepository
from app.core.users.repository import UserRepository
from app.infrastructure.database.schema_check import ensure_external_schema, missing_columns
from app.infrastructure.database.session import Database, after_commit
from tests.integration.conftest import PgServer, _build_app, client_for, clone_database_async
from tests.integration.test_auth import PASSWORD, bearer, login


def md5(value: str) -> str:
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()


async def password_of(connection: AsyncConnection, email: str) -> str:
    row = await connection.execute(
        text('SELECT password FROM "user" WHERE email = :email'), {"email": email}
    )
    return str(row.scalar_one())


async def test_unique_violation_exposes_constraint_name(connection: AsyncConnection) -> None:
    with pytest.raises(IntegrityError) as caught:
        async with connection.begin_nested():
            await connection.execute(
                text("INSERT INTO \"user\" (email) VALUES ('admin@example.com')")
            )
    violation = find_unique_violation(caught.value)
    assert violation is not None
    assert violation.constraint == "UQ_e12875dfb3b1d92d7d7c5377e22"


async def test_refresh_rejects_blocked_user(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    tokens = await login(client, "john.doe@example.com")
    await connection.execute(
        text('UPDATE "user" SET "statusId" = NULL WHERE email = :email'),
        {"email": "john.doe@example.com"},
    )
    response = await client.post("/api/v1/auth/refresh", headers=bearer(tokens["refreshToken"]))
    body = response.json()
    assert response.status_code == 422
    assert (body["code"], body["data"]) == (422, {"refresh": "User is not active"})


async def test_login_upgrades_bcrypt_hash(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    await connection.execute(
        text('UPDATE "user" SET password = :hash WHERE email = :email'),
        {"hash": BcryptHasher(rounds=4).hash(PASSWORD), "email": "john.doe@example.com"},
    )
    await login(client, "john.doe@example.com")
    assert (await password_of(connection, "john.doe@example.com")).startswith("$argon2")


async def test_rehash_keeps_concurrently_changed_password(
    app: FastAPI, connection: AsyncConnection
) -> None:
    stale = BcryptHasher(rounds=4).hash(PASSWORD)
    await connection.execute(
        text('UPDATE "user" SET password = :hash WHERE email = :email'),
        {"hash": stale, "email": "john.doe@example.com"},
    )
    context = app.state.context
    async with context.database.transaction() as session:
        user = await UserRepository(session).find_by_email("john.doe@example.com")
        assert user is not None
        changed = await context.passwords.hash_async(md5("Changed00"))
        await connection.execute(
            text('UPDATE "user" SET password = :hash WHERE id = :id'),
            {"hash": changed, "id": user.id},
        )
        assert await _service(context, session)._verify_password(user, PASSWORD)
    assert await password_of(connection, "john.doe@example.com") == changed


async def test_touch_failure_does_not_fail_request(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    tokens = await login(client, "john.doe@example.com")

    async def broken(*_: object) -> None:
        raise RuntimeError("touch failed")

    monkeypatch.setattr(SessionRepository, "touch", broken)
    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = await client.get("/api/v1/auth/me", headers=bearer(tokens["token"]))
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
                raise not_found()

        with pytest.raises(HttpError):
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

    async def test_session_cache_cleared_after_commit(self, app: FastAPI) -> None:
        context = app.state.context
        async with context.database.transaction() as session:
            sessions = _service(context, session).sessions
            await sessions.forget([42])
            await context.cache.set("jwt:session:42", {"statusId": 1})
            assert isinstance(session, AsyncSession)
        assert await context.cache.get("jwt:session:42") is None


class TestExternalSchema:
    async def test_complete_schema_passes(self, engine: AsyncEngine) -> None:
        await ensure_external_schema(engine)

    async def test_missing_column_is_reported(self, connection: AsyncConnection) -> None:
        await connection.execute(text('ALTER TABLE "session" DROP COLUMN "lastActiveAt"'))
        assert await missing_columns(connection) == ["session.lastActiveAt"]


async def test_user_list_orders_by_id_after_requested_sort(
    client: httpx.AsyncClient, connection: AsyncConnection
) -> None:
    await connection.execute(
        text(
            'INSERT INTO "user" (email, "firstName", "roleId", "statusId") '
            "SELECT 'order' || n || '@example.com', 'Same', 2, 1 FROM generate_series(1, 3) n"
        )
    )
    await connection.execute(
        text("UPDATE \"user\" SET \"firstName\" = 'Same' WHERE email = 'john.doe@example.com'")
    )
    token = (await login(client, "admin@example.com"))["token"]
    for query in ("", '&sort=[{"orderBy":"firstName","order":"asc"}]'):
        response = await client.get(f"/api/v1/users?limit=10{query}", headers=bearer(token))
        users = response.json()["data"]["data"]
        same = [user["id"] for user in users if user["firstName"] == "Same"]
        assert len(same) == 4
        assert same == sorted(same)
        if not query:
            assert [user["id"] for user in users] == sorted(user["id"] for user in users)


async def test_authenticated_request_needs_one_connection(
    pg_server: PgServer, template_db: str
) -> None:
    name = await clone_database_async(pg_server, f"single_{uuid.uuid4().hex[:12]}")
    settings = Settings.load().database.model_copy(
        update={"url": pg_server.url(name), "max_connections": 1}
    )
    database = Database.from_settings(settings)
    try:
        async with client_for(_build_app(database)) as http:
            tokens = await login(http, "john.doe@example.com")
            async with asyncio.timeout(10):
                response = await http.get("/api/v1/auth/me", headers=bearer(tokens["token"]))
        assert response.status_code == 200
    finally:
        await database.dispose()
