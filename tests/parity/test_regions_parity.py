from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tests.parity.conftest import Pair, Parity

URL = "/api/v1/regions"


def normalize(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: normalize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if key in {"createdAt", "updatedAt"} and value is not None:
        return "<time>"
    if key == "id" and isinstance(value, int):
        return "<id>"
    return value


def assert_same(pair: Pair) -> Any:
    python, nestjs = pair.both()
    assert python.status_code == nestjs.status_code, (python.text[:500], nestjs.text[:500])
    assert normalize(python.json()) == normalize(nestjs.json())
    return python.json()


@pytest.mark.parametrize(
    "path",
    [
        "/provinces",
        "/cities/110000000000",
        "/cities/440000000000",
        "/cities/999999999999",
        "/cities/11",
        "/cities/abcdefghijkl",
        "/districts/110100000000",
        "/districts/440300000000",
        "/districts/1101000000000",
        "/code/110101000000",
        "/code/440305000000",
        "/code/999999999998",
        "/code/x",
        "/tree",
        "",
        "?keyword=%E5%8C%97%E4%BA%AC",
        "?keyword=%E5%8D%97",
        "?keyword=%25",
        "?keyword=_",
        "?keyword=" + "x" * 51,
        "?parentCode=110000000000",
        "?parentCode=",
        "?parentCode=1",
        "?level=1",
        "?level=3&parentCode=110100000000",
        "?level=4",
        "?level=abc",
        "?level=1.5",
        "?level=",
        "?level=0",
        "?keyword=",
        "?keyword=%E5%8C%97&level=1&parentCode=110000000000",
    ],
)
@pytest.mark.parametrize("repeat", [1, 2])
def test_regions(parity: Parity, path: str, repeat: int) -> None:
    assert_same(parity.request("GET", URL + path, headers={"Accept-Language": "zh"}))
