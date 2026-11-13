import re
import time

lazy import httpx2 as httpx

from tests.e2e.conftest import (
    METRICS_TOKEN,
    ROOT,
    SEED_USER,
    bearer,
    confirm_hash,
    login,
    md5,
    register,
    unique_email,
    wait_for_mail,
)

PNG = b"\x89PNG\r\n\x1a\ne2e-image"
RATE_LIMIT_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset")
API_REGISTER_SAMPLE = re.compile(
    r'^http_requests_total\{app="FastAPI API",method="POST",'
    r'path="/api/v1/auth/email/register",status="200"\} \d',
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
    assert message["Subject"] == "Confirm email"
    assert message["From"]["Address"] == "noreply@example.com"
    confirmed = api.post("/api/v1/auth/email/confirm", json={"hash": confirm_hash(message)})
    assert confirmed.status_code == 200, confirmed.text

    session = login(api, email)
    me = api.get("/api/v1/auth/me", headers=bearer(session["token"]))
    assert me.status_code == 200, me.text
    assert me.json()["data"]["email"] == email
    assert me.json()["data"]["status"]["id"] == 1

    refreshed = api.post("/api/v1/auth/refresh", headers=bearer(session["refreshToken"]))
    assert refreshed.status_code == 200, refreshed.text
    rotated = refreshed.json()["data"]
    assert {"token", "refreshToken", "tokenExpires"} <= rotated.keys()
    reused = api.post("/api/v1/auth/refresh", headers=bearer(session["refreshToken"]))
    assert reused.status_code == 401

    logout = api.post("/api/v1/auth/logout", headers=bearer(rotated["token"]))
    assert logout.status_code == 200, logout.text
    assert api.get("/api/v1/auth/me", headers=bearer(rotated["token"])).status_code == 401


def test_public_and_private_file_round_trip(api: httpx.Client) -> None:
    token = login(api, SEED_USER)["token"]

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
    body = {"email": unique_email("throttle"), "password": md5("wrong")}
    responses = [api.post("/api/v1/auth/email/login", json=body) for _ in range(8)]

    accepted = [response for response in responses if response.status_code != 429]
    rejected = [response for response in responses if response.status_code == 429]
    assert accepted[0].status_code == 422
    assert accepted[0].headers["x-ratelimit-limit"] == "5"
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


def test_docs_json_is_baseline_bytes(api: httpx.Client) -> None:
    response = api.get("/docs-json")

    assert response.status_code == 200
    assert response.content == (ROOT / "openapi" / "nestjs-baseline.json").read_bytes()
