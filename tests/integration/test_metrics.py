import re
from typing import TYPE_CHECKING

import pytest

from app.infrastructure.database.session import Database
from app.infrastructure.telemetry import record_queue_job
from tests.integration.conftest import _build_app, client_for, error_of
from tests.integration.identity import auth_headers

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncConnection

TOKEN = "monitoring-token-0123456789"
DURATION = "http_server_request_duration_seconds"

type AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch) -> AppFactory:
    def factory(**env: str) -> FastAPI:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return _build_app(Database.bound_to(connection))

    return factory


def types(text: str) -> dict[str, str]:
    return dict(re.findall(r"^# TYPE (\S+) (\S+)$", text, re.MULTILINE))


def sample(text: str, name: str, **labels: str) -> float | None:
    total = None
    for line in text.splitlines():
        if not line.startswith(name + "{"):
            continue
        found = dict(re.findall(r'(\w+)="([^"]*)"', line.split("}", 1)[0]))
        if all(found.get(k) == v for k, v in labels.items()):
            total = (total or 0) + float(line.rsplit(" ", 1)[1])
    return total


class TestExposition:
    async def test_http_duration_uses_stable_semantic_conventions(
        self, make_app: AppFactory
    ) -> None:
        async with client_for(make_app()) as client:
            headers = await auth_headers(client)
            await client.get("/api/v1/me?debug=secret", headers=headers)
            response = await client.get("/metrics")

        body = response.text
        assert types(body)[DURATION] == "histogram"
        assert sample(
            body,
            f"{DURATION}_count",
            http_request_method="GET",
            http_route="/api/v1/me",
            http_response_status_code="200",
        )
        assert "secret" not in body
        assert "http_requests_total" not in body

    async def test_not_wrapped_in_envelope(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            response = await client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain; version=")
        assert not response.text.startswith("{")

    async def test_queue_job_duration_is_exported(self, make_app: AppFactory) -> None:
        app = make_app()
        record_queue_job("mail", "completed", 0.25)
        async with client_for(app) as client:
            body = (await client.get("/metrics")).text

        assert types(body)["queue_job_duration_seconds"] == "histogram"
        assert sample(body, "queue_job_duration_seconds_count", queue="mail", status="completed")

    async def test_otlp_exporter_has_no_metrics_route(self, make_app: AppFactory) -> None:
        async with client_for(make_app(OTEL_METRICS_EXPORTER="otlp")) as client:
            assert (await client.get("/metrics")).status_code == 404


class TestAccess:
    async def test_token_required_when_configured(self, make_app: AppFactory) -> None:
        async with client_for(make_app(METRICS_TOKEN=TOKEN)) as client:
            missing = await client.get("/metrics")
            wrong = await client.get("/metrics", headers={"Authorization": "Bearer nope"})
            ok = await client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})

        assert error_of(missing) == (401, "common.unauthorized")
        assert error_of(wrong) == (401, "common.unauthorized")
        assert ok.status_code == 200

    def test_not_in_openapi(self, make_app: AppFactory) -> None:
        assert "/metrics" not in make_app().openapi()["paths"]
