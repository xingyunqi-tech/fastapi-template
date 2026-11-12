"""nestjs `pagination` / `infinityPagination` 与对应响应模型（schema 名与 nestjs 一致）。"""

import math
from functools import cache
from typing import Any

from pydantic import BaseModel, create_model


def pagination[T](data: list[T], total: int, page: int, limit: int) -> dict[str, Any]:
    return {
        "list": data,
        "total": total,
        "page": page,
        "limit": limit,
        "totalPages": math.ceil(total / limit),
    }


def infinity_pagination[T](data: list[T], limit: int) -> dict[str, Any]:
    return {"data": data, "hasNextPage": len(data) == limit}


def _list_of(item: type[BaseModel]) -> Any:
    return list.__class_getitem__(item)


@cache
def pagination_response(item: type[BaseModel]) -> type[BaseModel]:
    """`PaginationResponse(Item)`：schema 名为 `Pagination{Item}ResponseDto`。"""
    return create_model(
        f"Pagination{item.__name__}ResponseDto",
        list=(_list_of(item), ...),
        total=(int, ...),
        page=(int, ...),
        limit=(int, ...),
        totalPages=(int, ...),
    )


@cache
def infinity_pagination_response(item: type[BaseModel]) -> type[BaseModel]:
    """`InfinityPaginationResponse(Item)`：schema 名为 `InfinityPagination{Item}ResponseDto`。"""
    return create_model(
        f"InfinityPagination{item.__name__}ResponseDto",
        data=(_list_of(item), ...),
        hasNextPage=(bool, ...),
    )
