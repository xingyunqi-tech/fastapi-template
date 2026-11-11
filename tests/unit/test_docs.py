from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.factory import create_app

REPO_BASELINE = Path(__file__).resolve().parents[2] / "openapi" / "nestjs-baseline.json"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings.load()))


def test_docs_json_returns_baseline_bytes(client: TestClient) -> None:
    response = client.get("/docs-json")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.content == REPO_BASELINE.read_bytes()


def test_docs_json_reads_configured_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_bytes(b'{"openapi":"3.0.0"}')
    monkeypatch.setenv("OPENAPI_BASELINE_PATH", str(baseline))

    response = TestClient(create_app(Settings.load())).get("/docs-json")

    assert response.content == b'{"openapi":"3.0.0"}'


def test_docs_page_points_to_docs_json(client: TestClient) -> None:
    response = client.get("/docs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'data-url="/docs-json"' in response.text


def test_fastapi_generated_openapi_is_not_exposed(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_docs_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    client = TestClient(create_app(Settings.load()))

    assert client.get("/docs-json").status_code == 404
    assert client.get("/docs").status_code == 404
