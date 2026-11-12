import uuid
from typing import Any

import pytest

lazy from tests.parity.conftest import Parity

CONTRACT_HEADERS = ("content-type",)
SECURITY_HEADERS = (
    "content-security-policy",
    "cross-origin-resource-policy",
    "origin-agent-cluster",
    "referrer-policy",
    "strict-transport-security",
    "x-content-type-options",
    "x-dns-prefetch-control",
    "x-download-options",
    "x-frame-options",
    "x-permitted-cross-domain-policies",
    "x-xss-protection",
)
CORS_HEADERS = (
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "access-control-expose-headers",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "vary",
)


def _same(parity: Parity, method: str, url: str, **kwargs: Any) -> Any:
    python, nestjs = parity.request(method, url, **kwargs).both()
    assert python.status_code == nestjs.status_code
    for header in CONTRACT_HEADERS:
        assert python.headers.get(header) == nestjs.headers.get(header), header
    assert python.json() == nestjs.json()
    return python.json()


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "/api/v1/nope"),
        ("GET", "/api/v1/nope?a=1%20b&c&d=%E4%B8%AD"),
        ("DELETE", "/api/nope/x"),
        ("PATCH", "/api/health?x=1"),
        ("POST", "/"),
        ("PUT", "/metrics/"),
    ],
)
def test_unmatched_routes(parity: Parity, method: str, url: str) -> None:
    python, nestjs = parity.request(method, url).both()

    assert python.status_code == nestjs.status_code == 404
    assert "allow" not in python.headers
    assert python.json() == nestjs.json()


@pytest.mark.parametrize("url", ["/", "/api/health", "/API/HEALTH", "/api/health/", "/Api/Health/"])
def test_matched_routes(parity: Parity, url: str) -> None:
    python, nestjs = parity.request("GET", url).both()

    assert python.status_code == nestjs.status_code == 200
    assert python.headers["content-type"] == nestjs.headers["content-type"]
    assert set(python.json()) == set(nestjs.json()) == {"code", "message", "data"}


def test_app_info(parity: Parity) -> None:
    _same(parity, "GET", "/")


def test_health_shape(parity: Parity) -> None:
    python, nestjs = (r.json()["data"] for r in parity.request("GET", "/api/health").both())

    assert set(python) == set(nestjs)
    assert set(python["memory"]) == set(nestjs["memory"])
    assert python["status"] == nestjs["status"] == "ok"
    assert len(python["timestamp"]) == len(nestjs["timestamp"])
    assert python["timestamp"].endswith("Z")


def test_head(parity: Parity) -> None:
    python, nestjs = parity.request("HEAD", "/api/health").both()

    assert python.status_code == nestjs.status_code == 200
    assert python.content == nestjs.content == b""


@pytest.mark.parametrize("request_id", ["abc.1:2-3", "x" * 128])
def test_request_id_echoed(parity: Parity, request_id: str) -> None:
    for response in parity.request("GET", "/", headers={"X-Request-Id": request_id}).both():
        assert response.headers["x-request-id"] == request_id


@pytest.mark.parametrize("request_id", [None, "bad id!", "x" * 129])
def test_request_id_generated(parity: Parity, request_id: str | None) -> None:
    headers = {"X-Request-Id": request_id} if request_id is not None else {}
    for response in parity.request("GET", "/api/v1/nope", headers=headers).both():
        generated = response.headers["x-request-id"]
        assert str(uuid.UUID(generated)) == generated


def test_security_headers(parity: Parity) -> None:
    python, nestjs = parity.request("GET", "/api/health").both()

    for header in SECURITY_HEADERS:
        assert python.headers.get(header) == nestjs.headers.get(header), header


@pytest.mark.parametrize("origin", [None, "http://a.example"])
def test_cors_simple_request(parity: Parity, origin: str | None) -> None:
    headers = {"Origin": origin} if origin else {}
    python, nestjs = parity.request("GET", "/", headers=headers).both()

    for header in CORS_HEADERS:
        assert python.headers.get(header) == nestjs.headers.get(header), header


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "http://a.example", "Access-Control-Request-Method": "POST"},
        {
            "Origin": "http://a.example",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "authorization,content-type,x-foo",
        },
    ],
)
def test_cors_preflight(parity: Parity, headers: dict[str, str]) -> None:
    python, nestjs = parity.request("OPTIONS", "/api/v1/auth/me", headers=headers).both()

    assert python.status_code == nestjs.status_code == 204
    assert python.content == nestjs.content == b""
    for header in CORS_HEADERS:
        assert python.headers.get(header) == nestjs.headers.get(header), header


@pytest.mark.parametrize(
    ("method", "url", "content"),
    [
        ("POST", "/api/v1/nope", b"x"),
        ("GET", "/", b"{bad"),
        ("PATCH", "/api/health", b"null"),
        ("POST", "/api/v1/nope", b"   "),
    ],
)
def test_invalid_json_rejected_before_routing(
    parity: Parity, method: str, url: str, content: bytes
) -> None:
    python, nestjs = parity.request(
        method, url, content=content, headers={"content-type": "application/json"}
    ).both()

    assert python.status_code == nestjs.status_code == 400
    assert python.json()["code"] == nestjs.json()["code"]
    assert python.json()["data"] == nestjs.json()["data"] is None


def test_oversized_body(parity: Parity) -> None:
    content = b"[" + b"1," * 140000 + b"1]"
    python, nestjs = parity.request(
        "POST", "/api/v1/nope", content=content, headers={"content-type": "application/json"}
    ).both()

    assert python.status_code == nestjs.status_code == 413
    assert python.json() == nestjs.json()


def test_percent_encoded_route_segment(parity: Parity) -> None:
    _same(parity, "GET", "/api/%68ealth")
