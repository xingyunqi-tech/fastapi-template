import re
from typing import TYPE_CHECKING

import pytest

from app.common.openapi import build_openapi
from app.infrastructure.database.session import Database
from app.infrastructure.metrics import METRICS_CONTENT_TYPE
from tests.integration.conftest import _build_app, client_for

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncConnection

NESTJS_CUSTOM_METRICS = {
    "http_requests_total": "counter",
    "http_request_duration_seconds": "histogram",
    "http_active_connections": "gauge",
    "database_query_duration_seconds": "histogram",
    "cache_hits_total": "counter",
    "cache_misses_total": "counter",
    "queue_jobs_total": "counter",
    "admin_alerts_total": "counter",
    "admin_alerts_deduplicated_total": "counter",
    "throttler_storage_fallback_total": "counter",
}
HTTP_BUCKETS = ["0.005", "0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1.0", "2.5", "5.0", "10.0"]
TOKEN = "monitoring-token-0123456789"

type AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(
    connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., FastAPI]:
    def factory(app_name: str | None = "Metrics App", **env: str) -> FastAPI:
        if app_name is None:
            monkeypatch.delenv("APP_NAME", raising=False)
        else:
            monkeypatch.setenv("APP_NAME", app_name)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return _build_app(Database.bound_to(connection))

    return factory


def types(text: str) -> dict[str, str]:
    return dict(re.findall(r"^# TYPE (\S+) (\S+)$", text, re.MULTILINE))


def sample(text: str, name: str, **labels: str) -> float | None:
    for line in text.splitlines():
        if not line.startswith(name + "{"):
            continue
        found = dict(re.findall(r'(\w+)="([^"]*)"', line.split("}", 1)[0]))
        if all(found.get(k) == v for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    return None


class TestExposition:
    async def test_app_label_defaults_to_nestjs_app(self, make_app: AppFactory) -> None:
        async with client_for(make_app(app_name=None)) as client:
            response = await client.get("/metrics")

        assert sample(response.text, "http_active_connections", app="nestjs-app") is not None

    async def test_names_types_and_labels_match_nestjs(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            await client.get("/")
            response = await client.get("/metrics")

        text = response.text
        assert response.status_code == 200
        assert response.headers["content-type"] == METRICS_CONTENT_TYPE
        declared = types(text)
        assert {k: declared[k] for k in NESTJS_CUSTOM_METRICS} == NESTJS_CUSTOM_METRICS
        assert "_created" not in text
        for line in text.splitlines():
            if line and not line.startswith("#"):
                assert 'app="Metrics App"' in line
        buckets = re.findall(
            r'^http_request_duration_seconds_bucket\{.*?le="([^"]+)".*?path="/",status="200"',
            text,
            re.MULTILINE,
        )
        assert buckets == [*HTTP_BUCKETS, "+Inf"]

    async def test_not_wrapped_and_without_api_prefix(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            response = await client.get("/metrics")
            prefixed = await client.get("/api/metrics")

        assert not response.text.startswith("{")
        assert prefixed.status_code == 404

    async def test_route_labels_use_express_templates(self, make_app: AppFactory) -> None:
        async with client_for(make_app()) as client:
            await client.get("/api/v1/users/12")
            await client.get("/api/v1/nope")
            await client.get("/api/v1/health/live")
            text = (await client.get("/metrics")).text

        assert sample(text, "http_requests_total", path="/api/v1/users/:id", status="401")
        assert sample(text, "http_requests_total", path="<unmatched>", status="404")
        assert sample(text, "http_requests_total", method="GET", path="/api/v1/health/live")
        assert sample(text, "http_active_connections") is not None

    async def test_database_and_cache_metrics_are_recorded(self, make_app: AppFactory) -> None:
        app = make_app()
        async with client_for(app) as client:
            await client.get("/api/v1/health/ready")
            await app.state.context.cache.get("missing-key")
            await app.state.context.cache.set("present-key", 1)
            await app.state.context.cache.get("present-key")
            text = (await client.get("/metrics")).text

        assert sample(
            text, "database_query_duration_seconds_count", operation="SELECT", table="unknown"
        )
        assert sample(text, "cache_misses_total", cache_name="cache")
        assert sample(text, "cache_hits_total", cache_name="cache")

    async def test_custom_path(self, make_app: AppFactory) -> None:
        async with client_for(make_app(METRICS_PATH="prom")) as client:
            custom = await client.get("/prom")
            default = await client.get("/metrics")

        assert custom.status_code == 200
        assert "http_requests_total" in custom.text
        assert default.status_code == 404

    async def test_disabled_keeps_endpoint_without_default_metrics(
        self, make_app: AppFactory
    ) -> None:
        async with client_for(make_app(METRICS_ENABLED="false")) as client:
            response = await client.get("/metrics")

        assert response.status_code == 200
        assert "http_requests_total" in types(response.text)
        assert "python_info" not in response.text


class TestAccess:
    async def test_token_required_when_configured(self, make_app: AppFactory) -> None:
        async with client_for(make_app(METRICS_TOKEN=TOKEN)) as client:
            missing = await client.get("/metrics")
            ok = await client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})

        assert missing.status_code == 403
        assert missing.json() == {"code": 403, "message": "Forbidden resource", "data": None}
        assert ok.status_code == 200

    async def test_not_in_openapi(self, make_app: AppFactory) -> None:
        document = build_openapi(make_app())

        assert "/metrics" not in document["paths"]
