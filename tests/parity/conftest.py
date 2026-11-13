import itertools
import os
import secrets
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
lazy from collections.abc import Iterator

import httpx2 as httpx
import pytest

PYTHON_URL = os.environ.get("PARITY_PYTHON_URL", "http://localhost:3000")
NESTJS_URL = os.environ.get("PARITY_NESTJS_URL", "http://localhost:3001")
PG_CONTAINERS = (
    os.environ.get("PARITY_PYTHON_PG_CONTAINER", "fastapi-temp-infra-postgres-1"),
    os.environ.get("PARITY_NESTJS_PG_CONTAINER", "parity-nestjs-postgres-1"),
)
RATE_LIMIT_HEADERS = (
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "retry-after",
)
_CLIENT_IPS = itertools.count(secrets.randbelow(1 << 24))


def _forwarded_for(request: httpx.Request) -> None:
    """每个请求使用不同的 10.0.0.0/8 地址，两侧均以 TRUST_PROXY=1 取作客户端 IP。"""
    n = next(_CLIENT_IPS) % (1 << 24)
    request.headers.setdefault("X-Forwarded-For", f"10.{n >> 16}.{(n >> 8) & 0xFF}.{n & 0xFF}")


def assert_same_rate_limit_headers(python: httpx.Response, nestjs: httpx.Response) -> None:
    """限流头集合一致且均为整数；X-RateLimit-Limit 取值一致。"""
    present = [
        {name for name in RATE_LIMIT_HEADERS if name in response.headers}
        for response in (python, nestjs)
    ]
    assert present[0] == present[1], (python.request.url, present)
    for response in (python, nestjs):
        for name in present[0]:
            assert response.headers[name].isdigit(), (name, response.headers[name])
    if "x-ratelimit-limit" in present[0]:
        assert python.headers["x-ratelimit-limit"] == nestjs.headers["x-ratelimit-limit"]


@dataclass(frozen=True, slots=True)
class Pair:
    python: httpx.Response
    nestjs: httpx.Response
    expect_throttled: bool = field(default=False, kw_only=True)

    def __post_init__(self) -> None:
        if not self.expect_throttled:
            for response in self.both():
                assert response.status_code != 429, (response.request.url, response.text)
        assert_same_rate_limit_headers(self.python, self.nestjs)

    def both(self) -> tuple[httpx.Response, httpx.Response]:
        return self.python, self.nestjs


class Parity:
    def __init__(self, python: httpx.Client, nestjs: httpx.Client) -> None:
        self.clients = (python, nestjs)

    def request(self, method: str, url: str, **kwargs: Any) -> Pair:
        python, nestjs = (client.request(method, url, **kwargs) for client in self.clients)
        return Pair(python, nestjs)


@dataclass(frozen=True, slots=True)
class TempUser:
    """两个后端各自注册的同一邮箱用户；ids 与 tokens 均为 (python, nestjs)。"""

    email: str
    ids: tuple[int, int]
    tokens: tuple[str, str]


def psql(container: str, sql: str) -> str:
    return subprocess.run(  # noqa: S603
        ["docker", "exec", container, "psql", "-U", "root", "-d", "api", "-qAtc", sql],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def drop_user(email: str) -> None:
    """从两边库删除该邮箱用户及其关联数据。"""
    owned = f"SELECT id FROM \"user\" WHERE email = '{email}'"  # noqa: S608
    sql = ";".join(
        f'DELETE FROM "{table}" WHERE {column} IN ({owned})'  # noqa: S608
        for table, column in (("feedback", '"userId"'), ("session", '"userId"'), ("user", "id"))
    )
    for container in PG_CONTAINERS:
        psql(container, sql)


@contextmanager
def temp_user(parity: Parity, prefix: str) -> Iterator[TempUser]:
    """在两边注册一次性用户，退出时删除。"""
    from tests.parity.test_auth_parity import PASSWORD, tokens

    email = f"parity.{prefix}.{int(time.time() * 1000)}@example.com"
    body = {"email": email, "password": PASSWORD, "firstName": "P", "lastName": "T"}
    try:
        for response in parity.request("POST", "/api/v1/auth/email/register", json=body).both():
            assert response.status_code == 200, response.text
        python_id, nestjs_id = (
            int(psql(container, f"SELECT id FROM \"user\" WHERE email = '{email}'"))  # noqa: S608
            for container in PG_CONTAINERS
        )
        yield TempUser(email, (python_id, nestjs_id), tokens(parity, email))
    finally:
        drop_user(email)


def config_exists(key: str) -> tuple[bool, bool]:
    sql = f"SELECT count(*) FROM config WHERE key = '{key}'"  # noqa: S608
    python, nestjs = (psql(container, sql).strip() != "0" for container in PG_CONTAINERS)
    return python, nestjs


def delete_configs(*keys: str, sides: tuple[bool, bool] = (True, True)) -> None:
    """按 sides 从 (python, nestjs) 库删除配置行。"""
    listed = ", ".join(f"'{key}'" for key in keys)
    sql = f"DELETE FROM config WHERE key IN ({listed})"  # noqa: S608
    for container, selected in zip(PG_CONTAINERS, sides, strict=True):
        if selected:
            psql(container, sql)


@pytest.fixture(scope="session")
def parity() -> Iterator[Parity]:
    options: dict[str, Any] = {
        "timeout": 10,
        "trust_env": False,
        "event_hooks": {"request": [_forwarded_for]},
    }
    with (
        httpx.Client(base_url=PYTHON_URL, **options) as python,
        httpx.Client(base_url=NESTJS_URL, **options) as nestjs,
    ):
        for client in (python, nestjs):
            client.get("/").raise_for_status()
        yield Parity(python, nestjs)


@pytest.fixture(scope="session")
def shared_user(parity: Parity) -> Iterator[TempUser]:
    with temp_user(parity, "shared") as user:
        yield user
