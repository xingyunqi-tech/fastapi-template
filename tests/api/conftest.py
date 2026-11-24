"""api：Granian 启动服务（testcontainers 的 Postgres 与 Redis），按入库的 OpenAPI 发请求。"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
lazy from collections.abc import Iterator

import httpx2 as httpx
import pytest

from tests.integration.conftest import PgServer, _execute, pg_server, redis_url

__all__ = ["pg_server", "redis_url"]

ROOT = Path(__file__).resolve().parents[2]
BIN = Path(sys.executable).parent
ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "Secret00"
STARTUP_TIMEOUT_S = 60.0


_TOKENS: list[str] = []


def current_token() -> str:
    """`server` fixture 登录种子管理员得到的 access token。"""
    return _TOKENS[-1]


@dataclass(frozen=True, slots=True)
class Server:
    base_url: str
    token: str


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(client: httpx.Client, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"服务进程退出：{process.returncode}")
        try:
            if client.get("/health/ready").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise AssertionError(f"服务未在 {STARTUP_TIMEOUT_S}s 内就绪")


@pytest.fixture(scope="session")
def server(
    pg_server: PgServer, redis_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Server]:
    asyncio.run(_execute(pg_server.dsn("postgres"), "CREATE DATABASE api_fuzz"))
    port = _free_port()
    env = {
        **os.environ,
        "DATABASE_URL": pg_server.url("api_fuzz"),
        "REDIS_URL": redis_url,
        "APP_PORT": str(port),
        "STORAGE_LOCAL_DIR": str(tmp_path_factory.mktemp("files")),
        "SEED_ADMIN_EMAIL": ADMIN_EMAIL,
        "SEED_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "LOG_LEVEL": "warn",
    }
    cwd = tmp_path_factory.mktemp("server")
    for name in ("alembic.ini", "migrations"):
        (cwd / name).symlink_to(ROOT / name)
    for command in ("migrate", "seed"):
        subprocess.run([BIN / "app", command], env=env, cwd=cwd, check=True)  # noqa: S603
    process = subprocess.Popen([sys.executable, "-m", "app"], env=env, cwd=cwd)
    base_url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as client:
            _wait_ready(client, process)
            login = client.post(
                "/api/v1/auth/email/login",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            )
            assert login.status_code == 200, login.text
        _TOKENS.append(login.json()["data"]["accessToken"])
        yield Server(base_url, _TOKENS[-1])
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
