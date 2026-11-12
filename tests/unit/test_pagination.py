from pydantic import BaseModel

from app.common.pagination import (
    infinity_pagination,
    infinity_pagination_response,
    pagination,
    pagination_response,
)


class UserDto(BaseModel):
    id: int


def test_pagination_shape() -> None:
    assert pagination([1, 2], total=21, page=2, limit=10) == {
        "list": [1, 2],
        "total": 21,
        "page": 2,
        "limit": 10,
        "totalPages": 3,
    }


def test_infinity_pagination_has_next_page_when_full() -> None:
    assert infinity_pagination([1, 2], limit=2) == {"data": [1, 2], "hasNextPage": True}
    assert infinity_pagination([1], limit=2) == {"data": [1], "hasNextPage": False}


def test_response_model_names_match_nestjs() -> None:
    assert pagination_response(UserDto).__name__ == "PaginationUserDtoResponseDto"
    assert infinity_pagination_response(UserDto).__name__ == "InfinityPaginationUserDtoResponseDto"
    assert set(pagination_response(UserDto).model_fields) == {
        "list",
        "total",
        "page",
        "limit",
        "totalPages",
    }
