"""e2e：ci 组合（base + postgres + redis + ci）以生产镜像起完整服务栈，黑盒访问。

`E2E_KEEP_STACK=1` 时结束后保留服务栈（项目名 `E2E_PROJECT`，默认 fastapi-e2e）；
设置 `CI` 时启动或用例失败后也保留，供 workflow 收集容器日志。
"""

import hashlib
import ipaddress
import os
import random
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
lazy from collections.abc import Iterator

import httpx2 as httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = os.environ.get("E2E_PROJECT", "fastapi-e2e")
COMPOSE_FILES = ("base", "postgres", "redis", "ci")
METRICS_TOKEN = "ci_metrics_token_0123456789abcdef"
SEED_USER = "john.doe@example.com"
CLIENT_NETWORK = ipaddress.ip_network("198.18.0.0/15")
MAIL_TIMEOUT_S = 30.0


def md5(text: str) -> str:
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


PASSWORD = md5("Secret00")


def compose(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    files = [arg for name in COMPOSE_FILES for arg in ("-f", f"docker/docker-compose.{name}.yaml")]
    return subprocess.run(  # noqa: S603
        ["docker", "compose", *files, "-p", PROJECT, *args],  # noqa: S607
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture,
    )


def published_url(service: str, port: int) -> str:
    return "http://" + compose("port", service, str(port), capture=True).stdout.strip()


@dataclass(frozen=True, slots=True)
class Stack:
    api_url: str
    mailpit_url: str


@pytest.fixture(scope="session")
def stack(request: pytest.FixtureRequest) -> Iterator[Stack]:
    started = False
    try:
        try:
            compose("up", "--build", "--detach", "--wait", "--wait-timeout", "300")
        except subprocess.CalledProcessError:
            compose("logs", "--no-color", "--tail", "200")
            raise
        started = True
        yield Stack(published_url("api", 3000), published_url("mailpit", 8025))
    finally:
        if started and request.session.testsfailed:
            compose("logs", "--no-color", "--tail", "200")
        failed = not started or bool(request.session.testsfailed)
        keep = os.environ.get("E2E_KEEP_STACK") == "1" or (failed and bool(os.environ.get("CI")))
        if not keep:
            compose("down", "--volumes", "--remove-orphans")


def random_client_ip() -> str:
    offset = random.randrange(1, CLIENT_NETWORK.num_addresses - 1)  # noqa: S311
    return str(CLIENT_NETWORK[offset])


@pytest.fixture
def api(stack: Stack) -> Iterator[httpx.Client]:
    """每个用例使用独立的 X-Forwarded-For（TRUST_PROXY=1），限流计数互不影响。"""
    with httpx.Client(
        base_url=stack.api_url,
        headers={"X-Forwarded-For": random_client_ip()},
        timeout=10,
        trust_env=False,
    ) as client:
        yield client


@pytest.fixture
def mailpit(stack: Stack) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=stack.mailpit_url, timeout=10, trust_env=False) as client:
        yield client


def unique_email(prefix: str) -> str:
    return f"e2e.{prefix}.{time.time_ns()}@example.com"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(api: httpx.Client, email: str, password: str = PASSWORD) -> dict[str, Any]:
    response = api.post("/api/v1/auth/email/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def register(api: httpx.Client, email: str) -> None:
    body = {"email": email, "password": PASSWORD, "firstName": "E2E", "lastName": "Tester"}
    response = api.post("/api/v1/auth/email/register", json=body)
    assert response.status_code == 200, response.text


def wait_for_mail(mailpit: httpx.Client, email: str) -> dict[str, Any]:
    deadline = time.monotonic() + MAIL_TIMEOUT_S
    while time.monotonic() < deadline:
        found = mailpit.get("/api/v1/search", params={"query": f'to:"{email}"'}).json()
        if found["messages"]:
            message: dict[str, Any] = mailpit.get(
                f"/api/v1/message/{found['messages'][0]['ID']}"
            ).json()
            return message
        time.sleep(0.5)
    raise AssertionError(f"{MAIL_TIMEOUT_S}s 内 Mailpit 未收到发往 {email} 的邮件")


def confirm_hash(message: dict[str, Any]) -> str:
    text: str = message["Text"]
    link = urlsplit(text.split(maxsplit=1)[0])
    assert link.path == "/confirm-email", text
    return parse_qs(link.query)["hash"][0]
