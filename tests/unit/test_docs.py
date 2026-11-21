import json
lazy from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli import cli
from app.cli.openapi import render
from app.common.openapi import OPENAPI_PATH
from app.config import Settings
from app.factory import create_app
from tests.conftest import use_production


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings.load()))


def test_openapi_json_is_served(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    body = response.json()
    assert body["openapi"] == "3.1.0"
    assert body["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"


def test_docs_page_points_to_openapi_json(client: TestClient) -> None:
    response = client.get("/docs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'data-url="/openapi.json"' in response.text


def test_redoc_is_not_exposed(client: TestClient) -> None:
    assert client.get("/redoc").status_code == 404


def test_docs_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    use_production(monkeypatch)
    client = TestClient(create_app(Settings.load()))

    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_committed_openapi_matches_code() -> None:
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render()


def test_export_check_fails_on_drift(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    target.write_text("{}\n", encoding="utf-8")

    result = CliRunner().invoke(cli, ["openapi", "export", "--check", "--output", str(target)])

    assert result.exit_code == 1


def test_export_writes_file(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"

    result = CliRunner().invoke(cli, ["openapi", "export", "--output", str(target)])

    assert result.exit_code == 0
    assert json.loads(target.read_text(encoding="utf-8"))["openapi"] == "3.1.0"


def test_operations_declare_errors_and_security() -> None:
    schema = json.loads(render())
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            responses = operation["responses"]
            assert responses["default"]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }, f"{method} {path}"
            if "422" in responses:
                assert responses["422"]["content"]["application/json"]["schema"] == {
                    "$ref": "#/components/schemas/ValidationErrorResponse"
                }
            if "401" in responses:
                assert operation["security"] == [{"bearerAuth": []}]
    assert "HTTPValidationError" not in schema["components"]["schemas"]


def test_envelope_fields_are_required() -> None:
    schemas = json.loads(render())["components"]["schemas"]

    assert schemas["UserResponse"]["required"] == ["code", "message", "data"]
    assert set(schemas["ErrorResponse"]["required"]) == {
        "code",
        "message",
        "errorKey",
        "data",
        "requestId",
    }
