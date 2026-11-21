"""约定 §2.1、§2.2、§2.4：成功与错误信封、错误码登记表。"""

import re
from pathlib import Path

lazy from fastapi.testclient import TestClient

from app.common.errors import ERRORS
from tests.conventions.probe import PROBE

CONVENTIONS = Path(__file__).resolve().parents[2] / "docs" / "platform-conventions.md"
_ROW = re.compile(r"^\| `([a-z]+\.[A-Za-z]+)` \| (\d{3}) \| (\d{5}) \|", re.M)


def registry_table() -> dict[str, tuple[int, int]]:
    text = CONVENTIONS.read_text(encoding="utf-8")
    section = text.split("### 2.4 错误码", 1)[1].split("\n## ", 1)[0]
    return {key: (int(status), int(code)) for key, status, code in _ROW.findall(section)}


def test_registry_matches_conventions_table() -> None:
    table = registry_table()

    assert table
    assert {key: (spec.status, spec.code) for key, spec in ERRORS.items()} == table


def test_codes_are_unique_and_derived_from_status() -> None:
    codes = [spec.code for spec in ERRORS.values()]

    assert len(codes) == len(set(codes))
    assert all(spec.code // 100 == spec.status for spec in ERRORS.values())


def test_success_envelope(client: TestClient) -> None:
    response = client.post(f"{PROBE}/echo", json={"email": "a@b.co", "name": "abc"})

    assert response.status_code == 201
    body = response.json()
    assert list(body) == ["code", "message", "data"]
    assert (body["code"], body["message"]) == (0, "ok")
    assert body["data"]["email"] == "a@b.co"


def test_null_data_is_explicit(client: TestClient) -> None:
    response = client.delete(f"{PROBE}/echo")

    assert response.json() == {"code": 0, "message": "ok", "data": None}


def test_error_envelope_fields(client: TestClient) -> None:
    response = client.get(f"{PROBE}/items/{'0' * 8}-0000-7000-8000-{'0' * 12}")

    body = response.json()
    assert response.status_code == 404
    assert list(body) == ["code", "message", "errorKey", "data", "requestId"]
    assert (body["code"], body["errorKey"], body["data"]) == (40400, "common.notFound", None)
    assert body["requestId"] == response.headers["x-request-id"]


def test_uncaught_exception_is_internal_error_without_details(client: TestClient) -> None:
    response = client.get(f"{PROBE}/boom")

    body = response.json()
    assert response.status_code == 500
    assert (body["code"], body["errorKey"], body["data"]) == (50000, "common.internalError", None)
    assert "secret detail" not in response.text
    assert body["requestId"] == response.headers["x-request-id"]
