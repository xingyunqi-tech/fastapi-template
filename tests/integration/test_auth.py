import hashlib
from typing import Any

import pytest
import time_machine
lazy import httpx2 as httpx
lazy from fastapi import FastAPI

from app.integrations.mail import LoggingMailer


def md5(text: str) -> str:
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


PASSWORD = md5("Secret00")


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/email/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def sent_mail(app: FastAPI) -> LoggingMailer:
    mailer = app.state.context.mailer
    assert isinstance(mailer, LoggingMailer)
    return mailer


class TestEmailFlows:
    async def test_register_login_and_confirm(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        response = await client.post(
            "/api/v1/auth/email/register",
            json={
                "email": "New.User@Example.com",
                "password": PASSWORD,
                "firstName": "N",
                "lastName": "U",
            },
        )
        assert response.json() == {"code": 200, "message": "success"}

        data = await login(client, "new.user@example.com")
        assert data["user"]["status"] == {"id": 2, "name": "Inactive", "__entity": "StatusEntity"}
        assert list(data) == ["refreshToken", "token", "tokenExpires", "user"]

        [mail] = sent_mail(app).sent
        assert mail.template == "activation"
        confirm = await client.post("/api/v1/auth/email/confirm", json={"hash": mail.data["hash"]})
        assert confirm.status_code == 200

        again = await client.post("/api/v1/auth/email/confirm", json={"hash": mail.data["hash"]})
        assert again.json() == {
            "code": 422,
            "message": "Validation failed",
            "data": {"hash": "Email is already confirmed"},
        }

    async def test_register_duplicate_email(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/email/register",
            json={
                "email": "john.doe@example.com",
                "password": PASSWORD,
                "firstName": "J",
                "lastName": "D",
            },
        )
        assert response.status_code == 422
        assert response.json()["data"] == {"email": "Email already exists"}

    @pytest.mark.parametrize(
        ("email", "password", "expected"),
        [
            ("nobody@example.com", PASSWORD, {"email": "Not found"}),
            ("john.doe@example.com", md5("wrong"), {"password": "Incorrect password"}),
        ],
    )
    async def test_login_errors(
        self, client: httpx.AsyncClient, email: str, password: str, expected: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/v1/auth/email/login", json={"email": email, "password": password}
        )
        assert response.status_code == 422
        assert response.json()["data"] == expected

    async def test_login_accepts_uppercase_md5(self, client: httpx.AsyncClient) -> None:
        await login(client, "john.doe@example.com", PASSWORD.upper())

    async def test_forgot_and_reset_password(self, client: httpx.AsyncClient, app: FastAPI) -> None:
        tokens = await login(client, "john.doe@example.com")
        assert (
            await client.post(
                "/api/v1/auth/forgot/password", json={"email": "john.doe@example.com"}
            )
        ).status_code == 200
        [mail] = sent_mail(app).sent
        new_password = md5("NewSecret00")

        reset = await client.post(
            "/api/v1/auth/reset/password",
            json={"hash": mail.data["hash"], "password": new_password},
        )
        assert reset.status_code == 200
        reused = await client.post(
            "/api/v1/auth/reset/password",
            json={"hash": mail.data["hash"], "password": new_password},
        )
        assert reused.json()["data"] == {"hash": "Invalid verification link"}

        me = await client.get("/api/v1/auth/me", headers=bearer(tokens["token"]))
        assert me.status_code == 401
        await login(client, "john.doe@example.com", new_password)

    async def test_expired_confirm_hash(self, client: httpx.AsyncClient, app: FastAPI) -> None:
        with time_machine.travel(0, tick=False):
            await client.post(
                "/api/v1/auth/email/register",
                json={
                    "email": "old@example.com",
                    "password": PASSWORD,
                    "firstName": "O",
                    "lastName": "L",
                },
            )
        [mail] = sent_mail(app).sent
        response = await client.post("/api/v1/auth/email/confirm", json={"hash": mail.data["hash"]})
        assert response.json()["data"] == {"hash": "Verification link has expired"}


class TestSessions:
    async def test_list_revoke_and_logout(self, client: httpx.AsyncClient) -> None:
        first = await login(client, "john.doe@example.com")
        second = await login(client, "john.doe@example.com")

        listed = (await client.get("/api/v1/auth/sessions", headers=bearer(first["token"]))).json()[
            "data"
        ]
        assert [s["current"] for s in listed].count(True) == 1
        assert {s["ip"] for s in listed} == {"203.0.113.7"}
        other = next(s for s in listed if not s["current"])

        revoke = await client.delete(
            f"/api/v1/auth/sessions/{other['id']}", headers=bearer(first["token"])
        )
        assert revoke.json() == {"code": 200, "message": "success"}
        assert (
            await client.get("/api/v1/auth/me", headers=bearer(second["token"]))
        ).status_code == 401
        missing = await client.delete(
            f"/api/v1/auth/sessions/{other['id']}", headers=bearer(first["token"])
        )
        assert missing.json() == {"code": 404, "message": "Session not found", "data": None}

        assert (
            await client.post("/api/v1/auth/logout", headers=bearer(first["token"]))
        ).status_code == 200
        expired = await client.get("/api/v1/auth/me", headers=bearer(first["token"]))
        assert expired.json() == {"code": 401, "message": "Session expired", "data": None}

    async def test_refresh_rotation_and_replay(self, client: httpx.AsyncClient) -> None:
        tokens = await login(client, "john.doe@example.com")
        first_refresh = tokens["refreshToken"]

        rotated = await client.post("/api/v1/auth/refresh", headers=bearer(first_refresh))
        assert list(rotated.json()["data"]) == ["token", "refreshToken", "tokenExpires"]
        second_refresh = rotated.json()["data"]["refreshToken"]

        grace = await client.post("/api/v1/auth/refresh", headers=bearer(first_refresh))
        assert grace.status_code == 401
        still_valid = await client.post("/api/v1/auth/refresh", headers=bearer(second_refresh))
        assert still_valid.status_code == 200

        replay = await client.post("/api/v1/auth/refresh", headers=bearer(first_refresh))
        assert replay.status_code == 401
        revoked = await client.post(
            "/api/v1/auth/refresh", headers=bearer(still_valid.json()["data"]["refreshToken"])
        )
        assert revoked.status_code == 401

    async def test_change_password_revokes_other_sessions(self, client: httpx.AsyncClient) -> None:
        current = await login(client, "john.doe@example.com")
        other = await login(client, "john.doe@example.com")
        response = await client.put(
            "/api/v1/auth/password",
            json={"oldPassword": PASSWORD, "newPassword": md5("Changed00")},
            headers=bearer(current["token"]),
        )
        assert response.status_code == 200
        assert (
            await client.get("/api/v1/auth/me", headers=bearer(current["token"]))
        ).status_code == 200
        assert (
            await client.get("/api/v1/auth/me", headers=bearer(other["token"]))
        ).status_code == 401


class TestProfile:
    async def test_update_and_delete_me(self, client: httpx.AsyncClient) -> None:
        tokens = await login(client, "john.doe@example.com")
        updated = await client.put(
            "/api/v1/auth/me",
            json={"nickname": "Johnny", "birthday": "1990-05-06", "gender": 1},
            headers=bearer(tokens["token"]),
        )
        body = updated.json()["data"]
        assert (body["nickname"], body["birthday"], body["gender"]) == ("Johnny", "1990-05-06", 1)

        missing = await client.request(
            "DELETE", "/api/v1/auth/me", json={}, headers=bearer(tokens["token"])
        )
        assert missing.json()["data"] == {"oldPassword": "Old password is required"}
        deleted = await client.request(
            "DELETE",
            "/api/v1/auth/me",
            json={"oldPassword": PASSWORD},
            headers=bearer(tokens["token"]),
        )
        assert deleted.status_code == 200
        gone = await client.post(
            "/api/v1/auth/email/login", json={"email": "john.doe@example.com", "password": PASSWORD}
        )
        assert gone.json()["data"] == {"email": "Not found"}


class TestUsersAdmin:
    async def test_permissions(self, client: httpx.AsyncClient) -> None:
        john = await login(client, "john.doe@example.com")
        response = await client.get("/api/v1/users", headers=bearer(john["token"]))
        assert response.json() == {
            "code": 403,
            "message": "Missing permissions: user:list",
            "data": None,
        }
        assert (await client.get("/api/v1/users")).status_code == 401

    async def test_crud(self, client: httpx.AsyncClient) -> None:
        admin = await login(client, "admin@example.com")
        headers = bearer(admin["token"])
        created = await client.post(
            "/api/v1/users",
            json={
                "email": "made@example.com",
                "password": PASSWORD,
                "firstName": "M",
                "role": {"id": 2},
                "status": {"id": 1},
            },
            headers=headers,
        )
        user = created.json()["data"]
        assert user["role"] == {"id": 2}
        assert "password" not in user

        listed = await client.get(
            '/api/v1/users?limit=100&filters={"roles":[{"id":2}]}', headers=headers
        )
        assert {u["email"] for u in listed.json()["data"]["data"]} == {
            "john.doe@example.com",
            "made@example.com",
        }

        updated = await client.put(
            f"/api/v1/users/{user['id']}", json={"nickname": "n"}, headers=headers
        )
        assert updated.json()["data"]["role"] == {"id": 2, "name": "User", "__entity": "RoleEntity"}

        assert (
            await client.delete(f"/api/v1/users/{user['id']}", headers=headers)
        ).status_code == 200
        assert (await client.get(f"/api/v1/users/{user['id']}", headers=headers)).json()[
            "data"
        ] is None

    async def test_invalid_id_after_auth(self, client: httpx.AsyncClient) -> None:
        admin = await login(client, "admin@example.com")
        response = await client.get("/api/v1/users/abc", headers=bearer(admin["token"]))
        assert response.json() == {
            "code": 400,
            "message": "Bad request",
            "data": {"id": "Invalid id"},
        }


@pytest.mark.committed
async def test_concurrent_refresh_only_one_wins(committed_app: FastAPI) -> None:
    import asyncio

    from tests.integration.conftest import client_for

    async with client_for(committed_app) as client:
        tokens = await login(client, "john.doe@example.com")
        results = await asyncio.gather(
            *(
                client.post("/api/v1/auth/refresh", headers=bearer(tokens["refreshToken"]))
                for _ in range(4)
            )
        )
        assert sorted(r.status_code for r in results) == [200, 401, 401, 401]
        winner = next(r for r in results if r.status_code == 200).json()["data"]
        assert (
            await client.post("/api/v1/auth/refresh", headers=bearer(winner["refreshToken"]))
        ).status_code == 200
