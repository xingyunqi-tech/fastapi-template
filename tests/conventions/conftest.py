"""约定测试夹具：每个用例一个挂载探针路由的应用。"""

import pytest
from fastapi.testclient import TestClient
lazy from fastapi import FastAPI

from tests.conventions.probe import Counter, build_probe_app


@pytest.fixture
def app() -> FastAPI:
    Counter.calls = 0
    return build_probe_app()


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)
