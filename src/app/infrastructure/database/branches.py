"""Alembic 分支过滤：按表的 `info["branch"]` 限定迁移比较范围。"""

from typing import Any
lazy from collections.abc import Callable, Iterable

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
lazy from sqlalchemy.engine import Connection

from app.infrastructure.database.base import Branch
from app.infrastructure.database.models import Base

type IncludeObject = Callable[[Any, str | None, str, bool, Any], bool]


def parse_branches(value: str | None) -> frozenset[Branch]:
    """逗号分隔的分支名；空值为全部分支。"""
    if not value:
        return frozenset(Branch)
    names = {name.strip() for name in value.split(",")} - {""}
    if not names:
        return frozenset(Branch)
    unknown = names - set(Branch)
    if unknown:
        raise ValueError(f"未知分支：{', '.join(sorted(unknown))}")
    return frozenset(Branch(name) for name in names)


def table_filter(branches: Iterable[Branch]) -> IncludeObject:
    """只比较 ORM 中属于 `branches` 的表；库中多出的表不比较。"""
    allowed = {str(branch) for branch in branches}

    def include(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
        if type_ != "table":
            return True
        table = compare_to if reflected else obj
        return table is not None and table.info.get("branch") in allowed

    return include


def schema_diff(connection: Connection, branches: Iterable[Branch]) -> list[Any]:
    """当前 schema 与 ORM 在 `branches` 范围内的差异，空列表表示一致。"""
    context = MigrationContext.configure(
        connection,
        opts={
            "include_object": table_filter(branches),
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    return list(compare_metadata(context, Base.metadata))
