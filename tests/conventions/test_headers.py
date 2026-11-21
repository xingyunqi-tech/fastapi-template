"""约定 §2.2 401 头与 §6 请求头：X-Request-Id、Accept-Language、CORS、Idempotency-Key。"""

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
lazy from fastapi import FastAPI

from app.common.errors import ERRORS
from app.common.i18n import get_i18n
from app.common.idempotency import MEMORY_STORE_KEY
from app.common.security import Jwt
from app.config import Settings
from tests.conftest import use_production
from tests.conventions.probe import PROBE, build_probe_app

ME = f"{PROBE}/me"
ORDERS = f"{PROBE}/orders"


class TestUnauthorized:
    def test_missing_token(self, client: TestClient) -> None:
        response = client.get(ME)

        assert response.status_code == 401
        assert response.json()["errorKey"] == "common.unauthorized"
        assert response.headers["www-authenticate"] == "Bearer"

    def test_invalid_token(self, client: TestClient) -> None:
        response = client.get(ME, headers={"Authorization": "Bearer nope"})

        assert response.json()["errorKey"] == "common.unauthorized"
        assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'

    def test_expired_token(self, client: TestClient) -> None:
        secret = Settings.load().auth.secret.get_secret_value()
        token = Jwt.sign({"id": 1, "role": {"id": 1}, "sessionId": 1}, secret, "-1s")

        response = client.get(ME, headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 401
        assert response.json()["errorKey"] == "auth.tokenExpired"
        assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'


class TestRequestId:
    @pytest.mark.parametrize("value", ["abc-123", "a.b_c:d", "x" * 128])
    def test_valid_inbound_id_is_kept(self, client: TestClient, value: str) -> None:
        response = client.get(f"{PROBE}/types", headers={"X-Request-Id": value})

        assert response.headers["x-request-id"] == value

    @pytest.mark.parametrize("value", ["", "bad id", "x" * 129, "a/b"])
    def test_invalid_inbound_id_is_replaced_with_uuid7(
        self, client: TestClient, value: str
    ) -> None:
        response = client.get(f"{PROBE}/types", headers={"X-Request-Id": value})

        generated = response.headers["x-request-id"]
        assert generated != value
        assert uuid.UUID(generated).version == 7

    def test_error_body_echoes_request_id(self, client: TestClient) -> None:
        response = client.get(ME, headers={"X-Request-Id": "trace-1"})

        assert response.json()["requestId"] == "trace-1"


class TestAcceptLanguage:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            (None, "请先登录"),
            ("en", "Authentication required"),
            ("en-US,en;q=0.9", "Authentication required"),
            ("fr, en;q=0.5", "Authentication required"),
            ("en;q=0.3, zh-CN;q=0.8", "请先登录"),
            ("fr", "请先登录"),
        ],
    )
    def test_message_language(self, client: TestClient, header: str | None, expected: str) -> None:
        headers = {"Accept-Language": header} if header else {}

        assert client.get(ME, headers=headers).json()["message"] == expected

    def test_default_locale_setting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_DEFAULT_LOCALE", "en")

        response = TestClient(build_probe_app()).get(ME)

        assert response.json()["message"] == "Authentication required"


ALLOWED_HEADERS = {
    "authorization",
    "content-type",
    "accept-language",
    "x-request-id",
    "idempotency-key",
    "traceparent",
    "tracestate",
}
EXPOSED_HEADERS = {
    "x-request-id",
    "retry-after",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
}


def _preflight(client: TestClient, origin: str) -> dict[str, str]:
    response = client.options(
        ME,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Idempotency-Key",
        },
    )
    return dict(response.headers)


def _split(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


class TestCors:
    def test_any_origin_outside_production(self, client: TestClient) -> None:
        headers = _preflight(client, "https://anywhere.example")

        assert headers["access-control-allow-origin"] in {"*", "https://anywhere.example"}
        assert _split(headers["access-control-allow-headers"]) >= ALLOWED_HEADERS
        assert "access-control-allow-credentials" not in headers

    def test_exposed_headers(self, client: TestClient) -> None:
        response = client.get(f"{PROBE}/types", headers={"Origin": "https://a.example"})

        assert _split(response.headers["access-control-expose-headers"]) >= EXPOSED_HEADERS
        assert "access-control-allow-credentials" not in response.headers

    def test_production_uses_allow_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        use_production(monkeypatch)
        monkeypatch.setenv("CORS_ORIGINS", "https://app.example")
        client = TestClient(build_probe_app())

        allowed = _preflight(client, "https://app.example")
        denied = _preflight(client, "https://evil.example")

        assert allowed["access-control-allow-origin"] == "https://app.example"
        assert "access-control-allow-credentials" not in allowed
        assert denied.get("access-control-allow-origin") is None


class TestIdempotency:
    def test_replays_first_response(self, client: TestClient) -> None:
        headers = {"Idempotency-Key": "k1"}

        first = client.post(ORDERS, json={"qty": 1}, headers=headers)
        second = client.post(ORDERS, json={"qty": 1}, headers=headers)

        assert first.status_code == second.status_code == 200
        assert second.json() == first.json()
        assert first.json()["data"]["call"] == 1

    def test_without_key_runs_every_time(self, client: TestClient) -> None:
        client.post(ORDERS, json={"qty": 1})
        second = client.post(ORDERS, json={"qty": 1})

        assert second.json()["data"]["call"] == 2

    def test_different_body_is_mismatch(self, client: TestClient) -> None:
        headers = {"Idempotency-Key": "k2"}
        client.post(ORDERS, json={"qty": 1}, headers=headers)

        response = client.post(ORDERS, json={"qty": 2}, headers=headers)

        assert response.status_code == 422
        assert response.json()["errorKey"] == "common.idempotencyKeyMismatch"

    def test_4xx_is_replayed(self, client: TestClient) -> None:
        headers = {"Idempotency-Key": "k3"}

        first = client.post(ORDERS, json={"qty": 99}, headers=headers)
        second = client.post(ORDERS, json={"qty": 99}, headers=headers)

        assert first.status_code == second.status_code == 409
        replayed = second.json()
        assert replayed.pop("requestId") == second.headers["x-request-id"]
        original = first.json()
        original.pop("requestId")
        assert replayed == original

    def test_5xx_releases_key(self, client: TestClient) -> None:
        headers = {"Idempotency-Key": "k4"}

        assert client.post(ORDERS, json={"qty": 500}, headers=headers).status_code == 500
        retried = client.post(ORDERS, json={"qty": 500}, headers=headers)

        assert retried.status_code == 500
        assert retried.json()["errorKey"] == "common.internalError"

    def test_header_is_declared_in_openapi(self, client: TestClient) -> None:
        operation = client.get("/openapi.json").json()["paths"][ORDERS]["post"]

        assert {"name": "Idempotency-Key", "in": "header"}.items() <= next(
            p for p in operation["parameters"] if p["name"] == "Idempotency-Key"
        ).items()


class _PendingStore:
    def __init__(self, body: bytes) -> None:
        self.body_hash = hashlib.sha256(body).hexdigest()

    async def claim(self, key: str, value: dict[str, object], ttl_ms: int) -> bool:
        return False

    async def get(self, key: str) -> dict[str, object]:
        return {"bodyHash": self.body_hash, "pending": True}


def test_idempotency_in_progress_is_409(app: FastAPI, client: TestClient) -> None:
    body = b'{"qty":1}'
    setattr(app.state, MEMORY_STORE_KEY, _PendingStore(body))

    response = client.post(
        ORDERS,
        content=body,
        headers={"Idempotency-Key": "k5", "Content-Type": "application/json"},
    )

    assert response.status_code == 409
    assert response.json()["errorKey"] == "common.idempotencyInProgress"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("en;q=2, zh;q=0.5", "请先登录"),
        ("en;q=inf", "请先登录"),
        ("en;q=1.000", "Authentication required"),
    ],
)
def test_out_of_range_qvalue_is_ignored(client: TestClient, header: str, expected: str) -> None:
    assert client.get(ME, headers={"Accept-Language": header}).json()["message"] == expected


def test_translations_cover_every_error_key() -> None:
    i18n = get_i18n()

    assert set(i18n.supported_languages) == {"en", "zh"}
    for language in i18n.supported_languages:
        assert set(i18n.translations[language]["errors"]) == set(ERRORS), language
