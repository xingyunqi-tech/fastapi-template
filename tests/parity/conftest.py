import os
from dataclasses import dataclass
from typing import Any
lazy from collections.abc import Iterator

import httpx2 as httpx
import pytest

PYTHON_URL = os.environ.get("PARITY_PYTHON_URL", "http://localhost:3000")
NESTJS_URL = os.environ.get("PARITY_NESTJS_URL", "http://localhost:3001")


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


@pytest.fixture(scope="session")
def parity() -> Iterator[Parity]:
    with (
        httpx.Client(base_url=PYTHON_URL, timeout=10, trust_env=False) as python,
        httpx.Client(base_url=NESTJS_URL, timeout=10, trust_env=False) as nestjs,
    ):
        for client in (python, nestjs):
            client.get("/").raise_for_status()
        yield Parity(python, nestjs)
