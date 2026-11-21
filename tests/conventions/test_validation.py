"""约定 §2.3：校验错误、400 / 404 / 405 / 413 / 415。"""

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from tests.conventions.probe import PROBE, build_probe_app

if TYPE_CHECKING:
    from collections.abc import Iterator

ECHO = f"{PROBE}/echo"
VALID = {"email": "a@b.co", "name": "abc"}


def errors(response: Any) -> list[dict[str, Any]]:
    body = response.json()
    assert response.status_code == 422, response.text
    assert (body["code"], body["errorKey"]) == (42200, "common.validationFailed")
    result: list[dict[str, Any]] = body["data"]["errors"]
    return result


def rules(response: Any) -> dict[str, str]:
    return {item["field"]: item["rule"] for item in errors(response)}


@pytest.mark.parametrize(
    ("patch", "field", "rule", "params"),
    [
        ({"email": None}, "email", "type", None),
        ({"email": "nope"}, "email", "email", None),
        ({"phone": "123"}, "phone", "phone", None),
        ({"name": "a"}, "name", "minLength", {"minLength": 2}),
        ({"name": "abcde"}, "name", "maxLength", {"maxLength": 4}),
        ({"age": -1}, "age", "min", {"min": 0}),
        ({"age": 151}, "age", "max", {"max": 150}),
        ({"age": "x"}, "age", "type", None),
        ({"color": "blue"}, "color", "enum", {"values": ["light_blue", "red"]}),
        ({"tags": ["a", "b", "c"]}, "tags", "maxItems", {"maxItems": 2}),
        ({"lines": [{"qty": 0}]}, "lines[0].qty", "min", {"min": 1}),
        ({"lines": [{}]}, "lines[0].qty", "required", None),
        ({"ref": "x"}, "ref", "uuid", None),
        ({"at": "yesterday"}, "at", "format", None),
        ({"at": "2026-11-17T16:00:00"}, "at", "format", None),
        ({"at": 1700000000}, "at", "type", None),
        ({"age": "18"}, "age", "type", None),
        ({"age": True}, "age", "type", None),
        ({"phone": "13\uff1800000000"}, "phone", "phone", None),
    ],
)
def test_rule_mapping(
    client: TestClient, patch: dict[str, Any], field: str, rule: str, params: Any
) -> None:
    [item] = errors(client.post(ECHO, json={**VALID, **patch}))

    assert (item["field"], item["rule"]) == (field, rule)
    assert item.get("params") == params
    assert item["message"]


def test_missing_field_is_required(client: TestClient) -> None:
    assert rules(client.post(ECHO, json={"name": "abc"})) == {"email": "required"}


def test_every_failed_field_is_reported(client: TestClient) -> None:
    assert rules(client.post(ECHO, json={"email": "x", "name": "a", "age": -1})) == {
        "email": "email",
        "name": "minLength",
        "age": "min",
    }


def test_length_counts_code_points(client: TestClient) -> None:
    assert client.post(ECHO, json={**VALID, "name": "😀😀😀😀"}).status_code == 201


def test_unknown_fields_are_ignored(client: TestClient) -> None:
    response = client.post(ECHO, json={**VALID, "extra": 1})

    assert response.status_code == 201
    assert "extra" not in response.json()["data"]


def test_query_and_path_fields_are_prefixed(client: TestClient) -> None:
    assert rules(client.get(f"{PROBE}/page", params={"page": 0})) == {"query.page": "min"}
    assert rules(client.get(f"{PROBE}/items/abc")) == {"path.id": "uuid"}


def test_message_follows_accept_language(client: TestClient) -> None:
    zh = errors(client.post(ECHO, json={"name": "abc"}))
    en = errors(client.post(ECHO, json={"name": "abc"}, headers={"Accept-Language": "en"}))

    assert zh[0]["message"] != en[0]["message"]


def test_malformed_json_is_400(client: TestClient) -> None:
    response = client.post(ECHO, content=b"{", headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.json()["errorKey"] == "common.malformedJson"


def test_unsupported_content_type_is_415(client: TestClient) -> None:
    response = client.post(ECHO, content=b"email=a", headers={"Content-Type": "text/plain"})

    assert response.status_code == 415
    assert response.json()["errorKey"] == "common.unsupportedMediaType"


def test_body_over_limit_is_413(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODY_LIMIT", "64B")
    client = TestClient(build_probe_app())

    response = client.post(ECHO, json={**VALID, "tags": ["x" * 100]})

    assert response.status_code == 413
    assert response.json()["errorKey"] == "common.payloadTooLarge"


def test_unknown_path_is_404(client: TestClient) -> None:
    response = client.get(f"{PROBE}/nowhere")

    assert response.status_code == 404
    assert response.json()["errorKey"] == "common.notFound"


def test_wrong_method_is_405_with_allow(client: TestClient) -> None:
    response = client.put(ECHO, json=VALID)

    assert response.status_code == 405
    assert response.json()["errorKey"] == "common.methodNotAllowed"
    assert set(response.headers["allow"].replace(" ", "").split(",")) >= {"POST", "DELETE"}


@pytest.mark.parametrize(
    "body",
    [b'{"email":"a@b.co","name":"\xff\xfe"}', b'{"email":"a@b.co","name":"abc","age":NaN}', b"{"],
)
def test_invalid_json_documents_are_400(client: TestClient, body: bytes) -> None:
    response = client.post(ECHO, content=body, headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.json()["errorKey"] == "common.malformedJson"


def test_missing_body_is_reported_as_body(client: TestClient) -> None:
    assert rules(client.post(ECHO)) == {"body": "required"}


def test_union_field_path_has_no_branch_tag(client: TestClient) -> None:
    assert rules(client.post(f"{PROBE}/union", json={"value": [1]})) == {"value": "type"}


def test_exclusive_bound_reports_inclusive_limit(client: TestClient) -> None:
    [item] = errors(client.post(f"{PROBE}/union", json={"value": 1, "positive": 0}))

    assert (item["field"], item["rule"], item["params"]) == ("positive", "min", {"min": 1})


def test_chunked_body_over_limit_is_413(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODY_LIMIT", "64B")
    client = TestClient(build_probe_app())

    def chunks() -> Iterator[bytes]:
        yield b'{"email":"a@b.co","name":"abc","tags":["'
        yield b"x" * 100
        yield b'"]}'

    response = client.post(ECHO, content=chunks(), headers={"Content-Type": "application/json"})

    assert response.status_code == 413
    assert response.json()["errorKey"] == "common.payloadTooLarge"


def test_wrong_method_wins_over_body_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODY_LIMIT", "64B")
    client = TestClient(build_probe_app())

    response = client.put(ECHO, json={**VALID, "tags": ["x" * 100]})

    assert response.status_code == 405


def test_allow_lists_exact_methods(client: TestClient) -> None:
    assert client.put(ECHO, json=VALID).headers["allow"] == "DELETE, POST"
    assert client.post(f"{PROBE}/types").headers["allow"] == "GET, HEAD"


def test_head_mirrors_get_without_body(client: TestClient) -> None:
    response = client.head(f"{PROBE}/types")

    assert response.status_code == 200
    assert response.content == b""


@pytest.mark.parametrize("path", [f"{PROBE}/types/", f"{PROBE}/TYPES"])
def test_path_match_is_exact(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert response.json()["requestId"] == response.headers["x-request-id"]
