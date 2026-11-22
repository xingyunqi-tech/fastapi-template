import re
import time

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
API_REGISTER_SAMPLE = re.compile(
    r'^http_requests_total\{app="FastAPI API",method="POST",'
    r'path="/api/v1/auth/email/register",status="201"\} \d',
    re.MULTILINE,
)
WORKER_MAIL_SAMPLE = re.compile(
    r'^queue_jobs_total\{app="FastAPI API",queue_name="mail",status="completed"\} \d',
    re.MULTILINE,
)


def test_health_ready(api: httpx.Client) -> None:
    response = api.get("/api/v1/health/ready")

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    data = response.json()["data"]
    assert data["status"] == "ok"
    assert data["details"]["database"]["status"] == "up"


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


def test_public_and_private_file_round_trip(api: httpx.Client) -> None:
    token = login(api, SEED_USER)["accessToken"]

    public = api.post(
        "/api/v1/files/upload",
        params={"purpose": "image"},
        headers=bearer(token),
        files={"file": ("e2e.png", PNG, "image/png")},
    )
    assert public.status_code == 201, public.text
    name = public.json()["data"]["file"]["path"].rsplit("/", 1)[-1]
    download = api.get(f"/api/v1/files/{name}")
    assert download.status_code == 200
    assert download.headers["content-type"] == "image/png"
    assert download.content == PNG

    private = api.post(
        "/api/v1/files/upload",
        params={"purpose": "private-image"},
        headers=bearer(token),
        files={"file": ("e2e.png", PNG, "image/png")},
    )
    assert private.status_code == 201, private.text
    file_id = private.json()["data"]["file"]["id"]
    assert api.get(f"/api/v1/files/private/{file_id}").status_code == 401
    owned = api.get(f"/api/v1/files/private/{file_id}", headers=bearer(token))
    assert owned.status_code == 200
    assert owned.content == PNG


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


def test_metrics_include_api_and_worker_samples(api: httpx.Client, mailpit: httpx.Client) -> None:
    assert api.get("/metrics").status_code == 403

    email = unique_email("metrics")
    register(api, email)
    wait_for_mail(mailpit, email)

    deadline = time.monotonic() + 10
    while True:
        response = api.get("/metrics", headers=bearer(METRICS_TOKEN))
        assert response.status_code == 200
        if WORKER_MAIL_SAMPLE.search(response.text) or time.monotonic() > deadline:
            break
        time.sleep(0.5)

    assert response.headers["content-type"].startswith("text/plain")
    assert API_REGISTER_SAMPLE.search(response.text), response.text
    assert WORKER_MAIL_SAMPLE.search(response.text), response.text


def test_openapi_json_is_served(api: httpx.Client) -> None:
    response = api.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["openapi"] == "3.1.0"
