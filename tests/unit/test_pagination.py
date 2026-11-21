import pytest
from fastapi.exceptions import RequestValidationError

from app.common.pagination import (
    CursorParams,
    Page,
    PageParams,
    SortField,
    encode_cursor,
    parse_sort,
)


def test_page_params_offset() -> None:
    params = PageParams(page=3, page_size=20)

    assert params.offset == 40


def test_page_of_dumps_camel_case() -> None:
    page = Page[int].of([1, 2], 42, PageParams(page=2, page_size=2))

    assert page.model_dump(mode="json") == {"items": [1, 2], "total": 42, "page": 2, "pageSize": 2}


def test_cursor_round_trip() -> None:
    cursor = encode_cursor("2026-11-17T08:00:00.000Z", "019a8f3c-6b1e-7d2a-9c4f-1e2d3c4b5a69")

    assert "=" not in cursor
    assert CursorParams(cursor=cursor).decoded() == (
        "2026-11-17T08:00:00.000Z",
        "019a8f3c-6b1e-7d2a-9c4f-1e2d3c4b5a69",
    )
    assert CursorParams().decoded() is None


@pytest.mark.parametrize("cursor", ["%%%", "bm90LWpzb24", "WzFd"])
def test_invalid_cursor_is_query_validation_error(cursor: str) -> None:
    with pytest.raises(RequestValidationError) as caught:
        CursorParams(cursor=cursor).decoded()

    [error] = caught.value.errors()
    assert error["loc"] == ("query", "cursor")
    assert error["type"] == "format"


def test_parse_sort_reads_direction() -> None:
    assert parse_sort("createdAt,-email", ["createdAt", "email"]) == [
        SortField("createdAt", descending=False),
        SortField("email", descending=True),
    ]


@pytest.mark.parametrize("value", ["password", "email,email", "-", "email,"])
def test_parse_sort_rejects_unknown_or_duplicate(value: str) -> None:
    with pytest.raises(RequestValidationError) as caught:
        parse_sort(value, ["createdAt", "email"])

    [error] = caught.value.errors()
    assert error["loc"] == ("query", "sort")
    assert error["type"] == "enum"
