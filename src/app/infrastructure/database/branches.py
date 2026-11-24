"""Alembic 分支过滤：按表的 `info["branch"]` 限定迁移比较范围。"""

from typing import Any
lazy from collections.abc import Callable, Iterable

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
lazy from alembic.config import Config
lazy from sqlalchemy.engine import Connection

from app.infrastructure.database.base import Branch
from app.infrastructure.database.models import Base
lazy from app.config import Settings

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


def role_branches(settings: Settings) -> frozenset[Branch]:
    """签发方为全部分支；配置了数据库的验证方为 platform + modules；否则为空。"""
    if not settings.database.enabled:
        return frozenset()
    if settings.auth.is_issuer:
        return frozenset(Branch)
    return frozenset({Branch.PLATFORM, Branch.MODULES})


def migration_targets(settings: Settings) -> tuple[str, ...]:
    """依次执行的 upgrade 目标：签发方 `heads`，验证方 `platform@head`、`modules@head`。"""
    if settings.auth.is_issuer:
        return ("heads",)
    return (f"{Branch.PLATFORM}@head", f"{Branch.MODULES}@head")


def migration_problems(connection: Connection, config: Config, settings: Settings) -> list[str]:
    """未升级到本角色的目标版本，或 schema 与 ORM 不一致时列出问题；空列表表示一致。"""
    script = ScriptDirectory.from_config(config)
    expected = {
        revision.revision
        for target in migration_targets(settings)
        for revision in script.get_revisions(target)
    }
    current = MigrationContext.configure(
        connection, opts={"version_table_schema": settings.database.db_schema}
    ).get_current_heads()
    applied = {
        revision.revision for revision in script.revision_map.iterate_revisions(current, "base")
    }
    problems = [f"未升级到最新版本：{revision}" for revision in sorted(expected - applied)]
    problems.extend(
        f"schema 与模型不一致：{diff}" for diff in schema_diff(connection, role_branches(settings))
    )
    return problems
