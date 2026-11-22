import time
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
import time_machine
lazy import httpx2 as httpx
lazy from fastapi import FastAPI

from tests import keys
from tests.integration.conftest import error_of, rules_of
from tests.integration.identity import ADMIN_EMAIL, PASSWORD, USER_EMAIL, bearer, login, sent_mail


async def register(client: httpx.AsyncClient, email: str, **extra: Any) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/email/register", json={"email": email, "password": PASSWORD, **extra}
    )


def claims_of(access_token: str) -> dict[str, Any]:
    decoded: dict[str, Any] = jwt.decode(
        access_token, keys.PUBLIC_KEY, algorithms=["EdDSA"], audience=keys.AUDIENCE
    )
    return decoded


async def refresh(client: httpx.AsyncClient, refresh_token: str) -> httpx.Response:
    return await client.post("/api/v1/auth/refresh", json={"refreshToken": refresh_token})


async def me(client: httpx.AsyncClient, access_token: str) -> httpx.Response:
    return await client.get("/api/v1/me", headers=bearer(access_token))


class TestRegistrationAndLogin:
    async def test_register_returns_login_response_and_sends_verification(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        response = await register(client, " New.User@Example.com ", displayName="New")

        assert response.status_code == 201
        data = response.json()["data"]
        assert list(data) == ["accessToken", "refreshToken", "expiresIn", "tokenType", "user"]
        assert (data["expiresIn"], data["tokenType"]) == (900, "Bearer")
        user = data["user"]
        assert list(user) == [
            "id",
            "email",
            "emailVerified",
            "phone",
            "displayName",
            "avatarUrl",
            "status",
            "roles",
            "hasPassword",
            "createdAt",
            "updatedAt",
        ]
        assert (user["email"], user["emailVerified"], user["roles"]) == (
            "new.user@example.com",
            False,
            ["user"],
        )
        assert (user["status"], user["hasPassword"], user["displayName"]) == ("active", True, "New")
        [mail] = sent_mail(app).sent
        assert (mail.template, mail.to) == ("verify-email", "new.user@example.com")

    async def test_register_with_taken_email_is_conflict(self, client: httpx.AsyncClient) -> None:
        assert error_of(await register(client, USER_EMAIL)) == (409, "user.emailTaken")

    @pytest.mark.parametrize(
        ("password", "rule"), [("x" * 7, "minLength"), ("x" * 129, "maxLength")]
    )
    async def test_password_length_is_validated(
        self, client: httpx.AsyncClient, password: str, rule: str
    ) -> None:
        response = await client.post(
            "/api/v1/auth/email/register", json={"email": "a@example.com", "password": password}
        )
        assert rules_of(response) == {"password": rule}

    async def test_password_length_counts_code_points(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/email/register",
            json={"email": "cp@example.com", "password": "密码密码密码密码"},
        )
        assert response.status_code == 201

    @pytest.mark.parametrize(
        ("email", "password"), [(USER_EMAIL, "Wrong000"), ("nobody@example.com", PASSWORD)]
    )
    async def test_wrong_credentials_are_indistinguishable(
        self, client: httpx.AsyncClient, email: str, password: str
    ) -> None:
        response = await client.post(
            "/api/v1/auth/email/login", json={"email": email, "password": password}
        )
        assert error_of(response) == (401, "auth.invalidCredentials")

    async def test_disabled_account_with_correct_password_is_forbidden(
        self, client: httpx.AsyncClient
    ) -> None:
        admin = (await login(client, ADMIN_EMAIL))["accessToken"]
        user_id = (await login(client, USER_EMAIL))["user"]["id"]
        await client.patch(
            f"/api/v1/users/{user_id}", json={"status": "disabled"}, headers=bearer(admin)
        )

        response = await client.post(
            "/api/v1/auth/email/login", json={"email": USER_EMAIL, "password": PASSWORD}
        )
        assert error_of(response) == (403, "auth.accountDisabled")

    async def test_public_route_ignores_invalid_authorization(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/api/v1/auth/email/login",
            json={"email": USER_EMAIL, "password": PASSWORD},
            headers=bearer("not-a-token"),
        )
        assert response.status_code == 200


class TestAccessToken:
    async def test_claims_and_header(self, client: httpx.AsyncClient) -> None:
        before = int(time.time())
        data = await login(client, USER_EMAIL)
        header = jwt.get_unverified_header(data["accessToken"])
        claims = claims_of(data["accessToken"])

        jwks = (await client.get("/.well-known/jwks.json")).json()
        assert header["alg"] == "EdDSA"
        assert header["kid"] == jwks["keys"][0]["kid"]
        assert set(claims) == {
            "iss",
            "aud",
            "sub",
            "sid",
            "roles",
            "email_verified",
            "auth_time",
            "iat",
            "exp",
            "jti",
        }
        assert (claims["iss"], claims["aud"], claims["sub"]) == (
            keys.ISSUER,
            keys.AUDIENCE,
            data["user"]["id"],
        )
        assert (claims["roles"], claims["email_verified"]) == (["user"], False)
        assert claims["exp"] - claims["iat"] == 900
        assert before <= claims["auth_time"] <= claims["iat"]

    async def test_jwks_lists_signing_key(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/.well-known/jwks.json")

        assert response.headers["cache-control"] == "max-age=300"
        [key] = response.json()["keys"]
        assert {k: key[k] for k in ("kty", "crv", "alg", "use")} == {
            "kty": "OKP",
            "crv": "Ed25519",
            "alg": "EdDSA",
            "use": "sig",
        }

    async def test_expired_access_token_asks_for_refresh(self, client: httpx.AsyncClient) -> None:
        token = (await login(client, USER_EMAIL))["accessToken"]
        with time_machine.travel(datetime.now(UTC) + timedelta(minutes=16), tick=False):
            response = await me(client, token)
        assert error_of(response) == (401, "auth.tokenExpired")
        assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'

    async def test_token_signed_by_unknown_key_is_rejected(self, client: httpx.AsyncClient) -> None:
        data = await login(client, USER_EMAIL)
        kid = jwt.get_unverified_header(data["accessToken"])["kid"]
        claims = claims_of(data["accessToken"])
        forged = jwt.encode(claims, keys.OTHER_SIGNING_KEY, algorithm="EdDSA", headers={"kid": kid})

        assert error_of(await me(client, forged)) == (401, "common.unauthorized")

    async def test_missing_token_is_unauthorized(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/me")
        assert error_of(response) == (401, "common.unauthorized")
        assert response.headers["www-authenticate"] == "Bearer"


class TestEmailVerification:
    async def test_verify_email_marks_user_verified(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        await register(client, "verify@example.com")
        [mail] = sent_mail(app).sent

        response = await client.post("/api/v1/auth/email/verify", json=mail.data)
        assert response.json()["data"] is None
        data = await login(client, "verify@example.com")
        assert data["user"]["emailVerified"] is True
        assert claims_of(data["accessToken"])["email_verified"] is True

        again = await client.post("/api/v1/auth/email/verify", json=mail.data)
        assert error_of(again) == (422, "auth.verificationTokenInvalid")

    async def test_new_token_replaces_previous_one(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        token = (await register(client, "twice@example.com")).json()["data"]["accessToken"]
        resend = await client.post("/api/v1/auth/email/verify/resend", headers=bearer(token))
        assert (resend.status_code, resend.json()["data"]) == (202, None)
        first, second = sent_mail(app).sent

        stale = await client.post("/api/v1/auth/email/verify", json=first.data)
        assert error_of(stale) == (422, "auth.verificationTokenInvalid")
        assert (await client.post("/api/v1/auth/email/verify", json=second.data)).status_code == 200

    async def test_expired_token_is_invalid(self, client: httpx.AsyncClient, app: FastAPI) -> None:
        await register(client, "late@example.com")
        [mail] = sent_mail(app).sent
        with time_machine.travel(datetime.now(UTC) + timedelta(hours=25), tick=False):
            response = await client.post("/api/v1/auth/email/verify", json=mail.data)
        assert error_of(response) == (422, "auth.verificationTokenInvalid")


class TestPasswordReset:
    async def test_unknown_email_is_accepted_without_mail(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        response = await client.post(
            "/api/v1/auth/password/forgot", json={"email": "nobody@example.com"}
        )
        assert (response.status_code, response.json()["data"]) == (202, None)
        assert list(sent_mail(app).sent) == []

    async def test_reset_changes_password_and_revokes_sessions(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        old = await login(client, USER_EMAIL)
        await client.post("/api/v1/auth/password/forgot", json={"email": USER_EMAIL})
        [mail] = sent_mail(app).sent
        assert mail.template == "reset-password"

        response = await client.post(
            "/api/v1/auth/password/reset", json={**mail.data, "password": "Changed00"}
        )
        assert response.status_code == 200
        assert error_of(await me(client, old["accessToken"])) == (401, "common.unauthorized")
        assert error_of(await refresh(client, old["refreshToken"])) == (
            401,
            "auth.refreshTokenInvalid",
        )
        await login(client, USER_EMAIL, "Changed00")

    async def test_reset_token_is_invalid_after_password_change(
        self, client: httpx.AsyncClient, app: FastAPI
    ) -> None:
        token = (await login(client, USER_EMAIL))["accessToken"]
        await client.post("/api/v1/auth/password/forgot", json={"email": USER_EMAIL})
        [mail] = sent_mail(app).sent
        await client.put(
            "/api/v1/me/password",
            json={"currentPassword": PASSWORD, "newPassword": "Changed00"},
            headers=bearer(token),
        )

        response = await client.post(
            "/api/v1/auth/password/reset", json={**mail.data, "password": "Another00"}
        )
        assert error_of(response) == (422, "auth.verificationTokenInvalid")


class TestRefresh:
    async def test_rotation_issues_new_pair(self, client: httpx.AsyncClient) -> None:
        first = await login(client, USER_EMAIL)
        response = await refresh(client, first["refreshToken"])

        data = response.json()["data"]
        assert data["refreshToken"] != first["refreshToken"]
        assert data["user"]["id"] == first["user"]["id"]
        old, new = claims_of(first["accessToken"]), claims_of(data["accessToken"])
        assert (new["sid"], new["auth_time"]) == (old["sid"], old["auth_time"])
        assert (await refresh(client, data["refreshToken"])).status_code == 200

    async def test_reuse_within_grace_period_returns_same_pair(
        self, client: httpx.AsyncClient
    ) -> None:
        first = await login(client, USER_EMAIL)
        rotated = (await refresh(client, first["refreshToken"])).json()["data"]
        replayed = (await refresh(client, first["refreshToken"])).json()["data"]

        assert (replayed["accessToken"], replayed["refreshToken"]) == (
            rotated["accessToken"],
            rotated["refreshToken"],
        )

    async def test_reuse_after_grace_period_revokes_session(
        self, client: httpx.AsyncClient
    ) -> None:
        first = await login(client, USER_EMAIL)
        rotated = (await refresh(client, first["refreshToken"])).json()["data"]
        with time_machine.travel(datetime.now(UTC) + timedelta(seconds=31), tick=False):
            reused = await refresh(client, first["refreshToken"])
            assert error_of(reused) == (401, "auth.refreshTokenReused")
            assert error_of(await me(client, rotated["accessToken"])) == (
                401,
                "common.unauthorized",
            )
            assert error_of(await refresh(client, rotated["refreshToken"])) == (
                401,
                "auth.refreshTokenInvalid",
            )

    async def test_unknown_token_is_invalid(self, client: httpx.AsyncClient) -> None:
        assert error_of(await refresh(client, "unknown")) == (401, "auth.refreshTokenInvalid")

    async def test_session_lifetime_is_not_extended_by_rotation(
        self, client: httpx.AsyncClient
    ) -> None:
        token = (await login(client, USER_EMAIL))["refreshToken"]
        with time_machine.travel(datetime.now(UTC) + timedelta(days=29), tick=False):
            token = (await refresh(client, token)).json()["data"]["refreshToken"]
        with time_machine.travel(datetime.now(UTC) + timedelta(days=31), tick=False):
            assert error_of(await refresh(client, token)) == (401, "auth.refreshTokenInvalid")

    async def test_logout_revokes_current_session(self, client: httpx.AsyncClient) -> None:
        data = await login(client, USER_EMAIL)
        response = await client.post("/api/v1/auth/logout", headers=bearer(data["accessToken"]))

        assert response.json()["data"] is None
        assert error_of(await me(client, data["accessToken"])) == (401, "common.unauthorized")
        assert error_of(await refresh(client, data["refreshToken"])) == (
            401,
            "auth.refreshTokenInvalid",
        )
