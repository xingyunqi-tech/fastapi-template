import re

lazy import httpx2 as httpx

from tests.e2e.conftest import (
    METRICS_TOKEN,
    SEED_USER,
    bearer,
    login,
    register,
    unique_email,
    verification_token,
    wait_for_mail,
)

PNG = b"\x89PNG\r\n\x1a\ne2e-image"
RATE_LIMIT_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")
HTTP_DURATION_SAMPLE = re.compile(
    r"^http_server_request_duration_seconds_count\{(?=[^}]*http_request_method=\"POST\")"
    r"(?=[^}]*http_route=\"/api/v1/auth/email/register\")"
    r"(?=[^}]*http_response_status_code=\"201\")[^}]*\} \d",
    re.MULTILINE,
)


def test_health_probes(api: httpx.Client) -> None:
    live = api.get("/health/live")
    ready = api.get("/health/ready")

    assert live.status_code == 200, live.text
    assert ready.status_code == 200, ready.text
    assert ready.json() == {"status": "ok"}


def test_email_registration_lifecycle(api: httpx.Client, mailpit: httpx.Client) -> None:
    email = unique_email("register")
    register(api, email)

    message = wait_for_mail(mailpit, email)
    assert message["Subject"] == "验证你的邮箱"
    assert message["From"]["Address"] == "noreply@example.com"
    verified = api.post("/api/v1/auth/email/verify", json={"token": verification_token(message)})
    assert verified.status_code == 200, verified.text

    session = login(api, email)
    me = api.get("/api/v1/me", headers=bearer(session["accessToken"]))
    assert me.status_code == 200, me.text
    assert (me.json()["data"]["email"], me.json()["data"]["emailVerified"]) == (email, True)

    jwks = api.get("/.well-known/jwks.json")
    assert jwks.status_code == 200
    assert jwks.json()["keys"][0]["alg"] == "EdDSA"

    refreshed = api.post("/api/v1/auth/refresh", json={"refreshToken": session["refreshToken"]})
    assert refreshed.status_code == 200, refreshed.text
    rotated = refreshed.json()["data"]
    assert {"accessToken", "refreshToken", "expiresIn", "tokenType"} <= rotated.keys()

    logout = api.post("/api/v1/auth/logout", headers=bearer(rotated["accessToken"]))
    assert logout.status_code == 200, logout.text
    assert api.get("/api/v1/me", headers=bearer(rotated["accessToken"])).status_code == 401
    stale = api.post("/api/v1/auth/refresh", json={"refreshToken": rotated["refreshToken"]})
    assert stale.status_code == 401


def test_file_upload_and_signed_content(api: httpx.Client) -> None:
    token = login(api, SEED_USER)["accessToken"]

    uploaded = api.post(
        "/api/v1/files",
        headers=bearer(token),
        data={"purpose": "avatar"},
        files={"file": ("e2e.png", PNG, "application/octet-stream")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file = uploaded.json()["data"]
    assert (file["contentType"], file["purpose"], file["size"]) == ("image/png", "avatar", len(PNG))

    content = api.get(file["url"])
    assert content.status_code == 200
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.content == PNG
    assert api.get(f"/api/v1/files/{file['id']}/content").status_code == 404


def test_rate_limit_headers_and_rejection(api: httpx.Client) -> None:
    body = {"email": unique_email("throttle")}
    responses = [api.post("/api/v1/auth/email/login", json=body) for _ in range(12)]

    accepted = [response for response in responses if response.status_code != 429]
    rejected = [response for response in responses if response.status_code == 429]
    assert accepted[0].status_code == 422
    assert accepted[0].headers["x-ratelimit-limit"] == "10"
    for response in accepted:
        for header in RATE_LIMIT_HEADERS:
            assert response.headers[header].isdigit(), (header, response.headers)
    assert rejected
    assert rejected[0].headers["retry-after"].isdigit()
    assert "x-ratelimit-limit" not in rejected[0].headers


def test_metrics_use_stable_http_semantic_conventions(api: httpx.Client) -> None:
    assert api.get("/metrics").status_code == 401

    register(api, unique_email("metrics"))
    response = api.get("/metrics", headers=bearer(METRICS_TOKEN))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert HTTP_DURATION_SAMPLE.search(response.text), response.text


def test_openapi_json_is_served(api: httpx.Client) -> None:
    response = api.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["openapi"] == "3.1.0"
