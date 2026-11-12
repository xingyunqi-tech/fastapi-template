import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
lazy from collections.abc import Iterator

import httpx2 as httpx
import pytest

PYTHON_URL = os.environ.get("PARITY_PYTHON_URL", "http://localhost:3000")
NESTJS_URL = os.environ.get("PARITY_NESTJS_URL", "http://localhost:3001")
PG_CONTAINERS = (
    os.environ.get("PARITY_PYTHON_PG_CONTAINER", "alembic-gen"),
    os.environ.get("PARITY_NESTJS_PG_CONTAINER", "nestjs-dev-postgres-1"),
)


@dataclass(frozen=True, slots=True)
class Pair:
    python: httpx.Response
    nestjs: httpx.Response

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
    with (
        httpx.Client(base_url=PYTHON_URL, timeout=10, trust_env=False) as python,
        httpx.Client(base_url=NESTJS_URL, timeout=10, trust_env=False) as nestjs,
    ):
        for client in (python, nestjs):
            client.get("/").raise_for_status()
        yield Parity(python, nestjs)


@pytest.fixture(scope="session")
def shared_user(parity: Parity) -> Iterator[TempUser]:
    with temp_user(parity, "shared") as user:
        yield user
