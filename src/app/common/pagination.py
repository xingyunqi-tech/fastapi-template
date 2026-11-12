"""nestjs `pagination` / `infinityPagination` 与对应响应模型（schema 名与 nestjs 一致）。"""

import math
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from typing import Any

from pydantic import BaseModel, create_model


def _pg_bigint(value: float) -> int:
    return int(Decimal(repr(value)).to_integral_value(ROUND_HALF_UP))


def offset_limit(page: float, limit: float) -> tuple[int, int]:
    """TypeORM skip / take 原样写入 SQL 后 PostgreSQL 实际使用的 OFFSET、LIMIT（四舍五入）。"""
    return _pg_bigint((page - 1) * limit), _pg_bigint(limit)


def lazy_total(found: int, page: float, limit: float) -> float | None:
    """TypeORM findAndCount 免计数查询的总数（skip + 本页条数）；须执行计数查询时返回 None。"""
    skip = (page - 1) * limit
    if found == limit or (found == 0 and skip > 0):
        return None
    return found + (skip if skip > 0 else 0)


def pagination[T](data: list[T], total: float, page: float, limit: float) -> dict[str, Any]:
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
