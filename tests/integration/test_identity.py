import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
import time_machine
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
lazy import httpx2 as httpx
lazy from fastapi import FastAPI
lazy from redis.asyncio import Redis
lazy from sqlalchemy.ext.asyncio import AsyncConnection

from app.cli.seed import seed
from app.common.errors import AppError
from app.config import Settings
from app.identity.users.models import UserStatus
from app.identity.users.schemas import UpdateUserRequest
from app.identity.users.service import UsersService
from app.security.revocation import Revocations
from tests.conftest import use_production
from tests.integration.conftest import client_for, error_of, rules_of
from tests.integration.identity import (
    ADMIN_EMAIL,
    PASSWORD,
    USER_EMAIL,
    auth_headers,
    bearer,
    login,
)
lazy from app.common.context import AppContext


async def create_user(
    client: httpx.AsyncClient, admin: dict[str, str], email: str, **extra: Any
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/users", json={"email": email, "password": PASSWORD, **extra}, headers=admin
    )
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def make_admin(client: httpx.AsyncClient, admin: dict[str, str], user_id: str) -> None:
    response = await client.put(
        f"/api/v1/users/{user_id}/roles", json={"roles": ["user", "admin"]}, headers=admin
    )
    assert response.status_code == 200, response.text


async def add_file(
    connection: AsyncConnection, owner: str, content_type: str, purpose: str = "avatar"
) -> UUID:
    file_id: UUID = await connection.scalar(
        text(
            "INSERT INTO files (owner_id, purpose, storage_key, name, content_type, size) "
            "VALUES (:owner, :purpose, md5(random()::text), 'a.png', :type, 1) RETURNING id"
        ),
        {"owner": owner, "purpose": purpose, "type": content_type},
    )
    return file_id


class TestMe:
    async def test_get_and_update_display_name(self, client: httpx.AsyncClient) -> None:
        headers = await auth_headers(client)
        assert (await client.get("/api/v1/me", headers=headers)).json()["data"]["email"] == (
            USER_EMAIL
        )

        response = await client.patch(
            "/api/v1/me", json={"displayName": " Johnny "}, headers=headers
        )
        assert response.json()["data"]["displayName"] == "Johnny"

    async def test_avatar_must_be_own_image(
        self, client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        data = await login(client, USER_EMAIL)
        headers = bearer(data["accessToken"])
        mine = await add_file(connection, data["user"]["id"], "image/png")
        others = await add_file(connection, str(UUID(int=2)), "image/png")
        attachment = await add_file(connection, data["user"]["id"], "image/png", "attachment")

        for file_id in (others, attachment, UUID(int=1)):
            response = await client.patch(
                "/api/v1/me", json={"avatarFileId": str(file_id)}, headers=headers
            )
            assert rules_of(response) == {"avatarFileId": "exists"}
        response = await client.patch(
            "/api/v1/me", json={"avatarFileId": str(mine)}, headers=headers
        )
        assert response.json()["data"]["avatarUrl"].startswith(f"/api/v1/files/{mine}/content?")
        cleared = await client.patch("/api/v1/me", json={"avatarFileId": None}, headers=headers)
        assert cleared.json()["data"]["avatarUrl"] is None

    async def test_change_password_requires_current_password(
        self, client: httpx.AsyncClient
    ) -> None:
        current = await login(client, USER_EMAIL)
        other = await login(client, USER_EMAIL)
        headers = bearer(current["accessToken"])

        missing = await client.put(
            "/api/v1/me/password", json={"newPassword": "Changed00"}, headers=headers
        )
        assert rules_of(missing) == {"currentPassword": "required"}
        wrong = await client.put(
            "/api/v1/me/password",
            json={"currentPassword": "Wrong000", "newPassword": "Changed00"},
            headers=headers,
        )
        assert error_of(wrong) == (422, "auth.passwordIncorrect")

        changed = await client.put(
            "/api/v1/me/password",
            json={"currentPassword": PASSWORD, "newPassword": "Changed00"},
            headers=headers,
        )
        assert changed.json()["data"] is None
        assert (await client.get("/api/v1/me", headers=headers)).status_code == 200
        revoked = await client.get("/api/v1/me", headers=bearer(other["accessToken"]))
        assert error_of(revoked) == (401, "common.unauthorized")
        await login(client, USER_EMAIL, "Changed00")

    async def test_setting_first_password_requires_recent_login(
        self, client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        data = await login(client, USER_EMAIL)
        await connection.execute(
            text("UPDATE users SET password_hash = NULL WHERE id = :id"),
            {"id": data["user"]["id"]},
        )
        headers = bearer(data["accessToken"])

        with time_machine.travel(datetime.now(UTC) + timedelta(minutes=11), tick=False):
            stale = await client.put(
                "/api/v1/me/password", json={"newPassword": "First000"}, headers=headers
            )
        assert error_of(stale) == (403, "auth.reauthRequired")
        fresh = await client.put(
            "/api/v1/me/password", json={"newPassword": "First000"}, headers=headers
        )
        assert fresh.status_code == 200
        await login(client, USER_EMAIL, "First000")


class TestSessions:
    async def test_list_marks_current_session(self, client: httpx.AsyncClient) -> None:
        first = await login(client, USER_EMAIL)
        await login(client, USER_EMAIL)

        response = await client.get("/api/v1/me/sessions", headers=bearer(first["accessToken"]))
        sessions = response.json()["data"]
        assert [s["current"] for s in sessions].count(True) == 1
        assert list(sessions[0]) == [
            "id",
            "userAgent",
            "ip",
            "createdAt",
            "lastActiveAt",
            "current",
        ]
        assert {s["ip"] for s in sessions} == {"203.0.113.7"}

    async def test_revoke_one_and_others(self, client: httpx.AsyncClient) -> None:
        first = await login(client, USER_EMAIL)
        second = await login(client, USER_EMAIL)
        third = await login(client, USER_EMAIL)
        headers = bearer(first["accessToken"])
        sessions = (await client.get("/api/v1/me/sessions", headers=headers)).json()["data"]
        target = next(s["id"] for s in sessions if not s["current"] and s["id"] != _sid(third))

        response = await client.delete(f"/api/v1/me/sessions/{target}", headers=headers)
        assert response.json()["data"] is None
        assert error_of(await client.get("/api/v1/me", headers=bearer(second["accessToken"]))) == (
            401,
            "common.unauthorized",
        )
        await client.post("/api/v1/me/sessions/revoke-others", headers=headers)
        assert error_of(await client.get("/api/v1/me", headers=bearer(third["accessToken"]))) == (
            401,
            "common.unauthorized",
        )
        remaining = (await client.get("/api/v1/me/sessions", headers=headers)).json()["data"]
        assert [s["current"] for s in remaining] == [True]

    async def test_revoking_unknown_or_foreign_session_is_not_found(
        self, client: httpx.AsyncClient
    ) -> None:
        mine = await auth_headers(client)
        admin = await login(client, ADMIN_EMAIL)
        for session_id in (_sid(admin), "019a8f3c-6b1e-7d2a-8000-000000000001"):
            response = await client.delete(f"/api/v1/me/sessions/{session_id}", headers=mine)
            assert error_of(response) == (404, "auth.sessionNotFound")


def _sid(login_data: dict[str, Any]) -> str:
    from tests.integration.test_auth import claims_of

    sid: str = claims_of(login_data["accessToken"])["sid"]
    return sid


class TestDeleteMe:
    async def test_delete_me_releases_email(self, client: httpx.AsyncClient) -> None:
        data = await login(client, USER_EMAIL)
        response = await client.delete("/api/v1/me", headers=bearer(data["accessToken"]))

        assert (response.status_code, response.json()["data"]) == (202, None)
        assert error_of(await client.get("/api/v1/me", headers=bearer(data["accessToken"]))) == (
            401,
            "common.unauthorized",
        )
        failed = await client.post(
            "/api/v1/auth/email/login", json={"email": USER_EMAIL, "password": PASSWORD}
        )
        assert error_of(failed) == (401, "auth.invalidCredentials")
        again = await client.post(
            "/api/v1/auth/email/register", json={"email": USER_EMAIL, "password": PASSWORD}
        )
        assert again.status_code == 201
        assert again.json()["data"]["user"]["id"] != data["user"]["id"]

    async def test_delete_me_requires_recent_login(self, client: httpx.AsyncClient) -> None:
        headers = await auth_headers(client)
        with time_machine.travel(datetime.now(UTC) + timedelta(minutes=11), tick=False):
            response = await client.delete("/api/v1/me", headers=headers)
        assert error_of(response) == (403, "auth.reauthRequired")

    async def test_last_admin_cannot_delete_self(self, client: httpx.AsyncClient) -> None:
        response = await client.delete(
            "/api/v1/me", headers=await auth_headers(client, ADMIN_EMAIL)
        )
        assert error_of(response) == (409, "role.lastAdmin")


class TestUserManagement:
    async def test_regular_user_is_forbidden(self, client: httpx.AsyncClient) -> None:
        headers = await auth_headers(client)
        for method, path in (("GET", "/api/v1/users"), ("GET", "/api/v1/roles")):
            response = await client.request(method, path, headers=headers)
            assert error_of(response) == (403, "common.forbidden")

    async def test_list_filters_and_sorts(self, client: httpx.AsyncClient) -> None:
        admin = await auth_headers(client, ADMIN_EMAIL)
        await create_user(client, admin, "zed@example.com", displayName="Zed")
        await create_user(client, admin, "amy@example.com", displayName="Amy")

        default = (await client.get("/api/v1/users?pageSize=2", headers=admin)).json()["data"]
        assert {u["email"] for u in default["items"]} == {"amy@example.com", "zed@example.com"}
        assert (default["total"], default["page"], default["pageSize"]) == (4, 1, 2)
        by_email = await client.get("/api/v1/users?sort=email", headers=admin)
        assert [u["email"] for u in by_email.json()["data"]["items"]] == [
            ADMIN_EMAIL,
            "amy@example.com",
            USER_EMAIL,
            "zed@example.com",
        ]
        keyword = await client.get("/api/v1/users?keyword=ZE", headers=admin)
        assert [u["email"] for u in keyword.json()["data"]["items"]] == ["zed@example.com"]
        admins = await client.get("/api/v1/users?role=admin", headers=admin)
        assert [u["email"] for u in admins.json()["data"]["items"]] == [ADMIN_EMAIL]
        bad_status = await client.get("/api/v1/users?status=gone", headers=admin)
        assert rules_of(bad_status) == {"query.status": "enum"}
        bad_sort = await client.get("/api/v1/users?sort=name", headers=admin)
        assert rules_of(bad_sort) == {"query.sort": "enum"}

    async def test_create_user_requires_email_or_phone(self, client: httpx.AsyncClient) -> None:
        admin = await auth_headers(client, ADMIN_EMAIL)
        response = await client.post("/api/v1/users", json={"displayName": "X"}, headers=admin)
        assert rules_of(response) == {"email": "required", "phone": "required"}

        created = await create_user(client, admin, "p@example.com", phone="13800000000")
        assert (created["phone"], created["roles"]) == ("+8613800000000", ["user"])
        taken = await client.post("/api/v1/users", json={"phone": "+8613800000000"}, headers=admin)
        assert error_of(taken) == (409, "user.phoneTaken")

    async def test_get_unknown_user_is_not_found(self, client: httpx.AsyncClient) -> None:
        admin = await auth_headers(client, ADMIN_EMAIL)
        response = await client.get(f"/api/v1/users/{UUID(int=7)}", headers=admin)
        assert error_of(response) == (404, "user.notFound")

    async def test_disable_revokes_sessions(self, client: httpx.AsyncClient) -> None:
        admin = await auth_headers(client, ADMIN_EMAIL)
        user = await login(client, USER_EMAIL)

        response = await client.patch(
            f"/api/v1/users/{user['user']['id']}",
            json={"status": "disabled", "displayName": "Off"},
            headers=admin,
        )
        assert (response.json()["data"]["status"], response.json()["data"]["displayName"]) == (
            "disabled",
            "Off",
        )
        revoked = await client.get("/api/v1/me", headers=bearer(user["accessToken"]))
        assert error_of(revoked) == (401, "common.unauthorized")

    async def test_delete_user(self, client: httpx.AsyncClient) -> None:
        admin = await auth_headers(client, ADMIN_EMAIL)
        user = await login(client, USER_EMAIL)

        response = await client.delete(f"/api/v1/users/{user['user']['id']}", headers=admin)
        assert response.status_code == 202
        gone = await client.get(f"/api/v1/users/{user['user']['id']}", headers=admin)
        assert error_of(gone) == (404, "user.notFound")

    async def test_set_roles_validates_and_revokes_sessions(
        self, client: httpx.AsyncClient
    ) -> None:
        admin = await auth_headers(client, ADMIN_EMAIL)
        user = await login(client, USER_EMAIL)
        path = f"/api/v1/users/{user['user']['id']}/roles"

        unknown = await client.put(path, json={"roles": ["user", "owner"]}, headers=admin)
        assert rules_of(unknown) == {"roles[1]": "exists"}
        missing = await client.put(path, json={"roles": ["admin"]}, headers=admin)
        assert rules_of(missing) == {"roles": "contains"}

        response = await client.put(path, json={"roles": ["admin", "user"]}, headers=admin)
        assert response.json()["data"]["roles"] == ["admin", "user"]
        revoked = await client.get("/api/v1/me", headers=bearer(user["accessToken"]))
        assert error_of(revoked) == (401, "common.unauthorized")
        promoted = await login(client, USER_EMAIL)
        assert promoted["user"]["roles"] == ["admin", "user"]
        users = await client.get("/api/v1/users", headers=bearer(promoted["accessToken"]))
        assert users.status_code == 200

    async def test_last_admin_is_protected(self, client: httpx.AsyncClient) -> None:
        data = await login(client, ADMIN_EMAIL)
        admin = bearer(data["accessToken"])
        admin_id = data["user"]["id"]

        disable = await client.patch(
            f"/api/v1/users/{admin_id}", json={"status": "disabled"}, headers=admin
        )
        assert error_of(disable) == (409, "role.lastAdmin")
        demote = await client.put(
            f"/api/v1/users/{admin_id}/roles", json={"roles": ["user"]}, headers=admin
        )
        assert error_of(demote) == (409, "role.lastAdmin")
        delete = await client.delete(f"/api/v1/users/{admin_id}", headers=admin)
        assert error_of(delete) == (409, "role.lastAdmin")
        assert (await client.get("/api/v1/me", headers=admin)).status_code == 200

    async def test_roles_and_permissions(self, client: httpx.AsyncClient) -> None:
        admin = await auth_headers(client, ADMIN_EMAIL)
        roles = (await client.get("/api/v1/roles", headers=admin)).json()["data"]
        permissions = (await client.get("/api/v1/permissions", headers=admin)).json()["data"]

        codes = [p["code"] for p in permissions]
        assert {"user:read", "user:write", "role:read", "role:write", "audit:read"} <= set(codes)
        assert [r["code"] for r in roles] == ["admin", "user"]
        assert roles[0]["permissions"] == codes
        assert sorted(roles[1]["permissions"]) == ["file:write", "note:read", "note:write"]


@pytest.mark.committed
async def test_concurrent_refresh_issues_one_pair(committed_app: FastAPI) -> None:
    async with client_for(committed_app) as client:
        token = (await login(client, USER_EMAIL))["refreshToken"]
        responses = await asyncio.gather(
            *(client.post("/api/v1/auth/refresh", json={"refreshToken": token}) for _ in range(5))
        )
    pairs = {(r.json()["data"]["accessToken"], r.json()["data"]["refreshToken"]) for r in responses}
    assert [r.status_code for r in responses] == [200] * 5
    assert len(pairs) == 1


@pytest.mark.committed
async def test_concurrently_disabling_two_admins_keeps_one(committed_app: FastAPI) -> None:
    context: AppContext = committed_app.state.context
    async with client_for(committed_app) as client:
        first = await login(client, ADMIN_EMAIL)
        headers = bearer(first["accessToken"])
        second = await create_user(client, headers, "second.admin@example.com")
        await make_admin(client, headers, second["id"])

    async def disable(user_id: str) -> str:
        try:
            async with context.db.transaction() as session:
                await UsersService(context, session).update(
                    UUID(user_id), UpdateUserRequest(status=UserStatus.DISABLED)
                )
        except AppError as error:
            return error.error_key
        return "ok"

    outcomes = await asyncio.gather(disable(first["user"]["id"]), disable(second["id"]))
    assert sorted(outcomes) == ["ok", "role.lastAdmin"]
    async with context.db.transaction() as session:
        active_admins = await session.scalar(
            text(
                "SELECT count(*) FROM user_roles r JOIN users u ON u.id = r.user_id "
                "WHERE r.role = 'admin' AND u.status = 'active' AND u.deleted_at IS NULL"
            )
        )
    assert active_admins == 1


@pytest.mark.committed
async def test_revocation_key_is_written_to_redis(committed_app: FastAPI) -> None:
    redis: Redis = committed_app.state.context.redis
    async with client_for(committed_app) as client:
        data = await login(client, USER_EMAIL)
        await client.post("/api/v1/auth/logout", headers=bearer(data["accessToken"]))
    key = f"auth:revoked:sid:{_sid(data)}"
    ttl_ms = await redis.pttl(key)
    assert 900_000 < ttl_ms <= 960_000


class TestSeed:
    async def test_skips_when_active_admin_exists(self, app: FastAPI) -> None:
        async with app.state.context.db.transaction() as session:
            assert await seed(session, app.state.context.settings) is False

    async def test_production_admin_comes_from_settings(
        self,
        app: FastAPI,
        client: httpx.AsyncClient,
        connection: AsyncConnection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await connection.execute(text("DELETE FROM user_roles WHERE role = 'admin'"))
        use_production(monkeypatch)
        monkeypatch.setenv("SEED_ADMIN_EMAIL", " Root@Example.com ")
        monkeypatch.setenv("SEED_ADMIN_PASSWORD", "Rooted00")

        async with app.state.context.db.transaction() as session:
            assert await seed(session, Settings.load()) is True
        data = await login(client, "root@example.com", "Rooted00")
        assert (data["user"]["roles"], data["user"]["emailVerified"]) == (["admin", "user"], True)

    async def test_production_without_settings_creates_nothing(
        self, app: FastAPI, connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await connection.execute(text("DELETE FROM user_roles WHERE role = 'admin'"))
        use_production(monkeypatch)

        async with app.state.context.db.transaction() as session:
            assert await seed(session, Settings.load()) is False


class _BrokenRedis:
    def pipeline(self, *, transaction: bool) -> _BrokenRedis:
        return self

    async def __aenter__(self) -> _BrokenRedis:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def set(self, *_: object, **__: object) -> None:
        return None

    async def execute(self) -> None:
        raise RedisConnectionError("down")

    async def exists(self, *_: object) -> int:
        raise RedisConnectionError("down")


class TestRedisFailure:
    @staticmethod
    def break_redis(app: FastAPI) -> None:
        context: AppContext = app.state.context
        context.revocations = Revocations(
            _BrokenRedis(),  # type: ignore[arg-type]
            context.settings.auth.access_ttl,
        )

    async def test_revocation_check_failure_is_503(
        self, app: FastAPI, client: httpx.AsyncClient
    ) -> None:
        headers = await auth_headers(client)
        self.break_redis(app)

        response = await client.get("/api/v1/me", headers=headers)
        assert error_of(response) == (503, "common.serviceUnavailable")
        assert response.headers["retry-after"] == "5"

    async def test_revocation_write_failure_is_503_and_rolls_back(
        self, app: FastAPI, client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        data = await login(client, USER_EMAIL)
        context: AppContext = app.state.context
        working = context.revocations
        self.break_redis(app)
        context.revocations.is_revoked = working.is_revoked  # type: ignore[method-assign]

        response = await client.post("/api/v1/auth/logout", headers=bearer(data["accessToken"]))
        assert error_of(response) == (503, "common.serviceUnavailable")
        assert response.headers["retry-after"] == "5"
        revoked = await connection.scalar(
            text("SELECT revoked_at FROM sessions WHERE id = :id"), {"id": _sid(data)}
        )
        assert revoked is None


async def test_invalid_forwarded_ip_is_stored_as_null(
    app: FastAPI, connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state.context.settings.app, "trusted_proxies", 1)
    async with client_for(app) as client:
        response = await client.post(
            "/api/v1/auth/email/login",
            json={"email": USER_EMAIL, "password": PASSWORD},
            headers={"X-Forwarded-For": "not-an-ip"},
        )
    assert response.status_code == 200
    ip = await connection.scalar(
        text("SELECT ip FROM sessions WHERE id = :id"), {"id": _sid(response.json()["data"])}
    )
    assert ip is None
