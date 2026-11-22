import io
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Mapped, mapped_column

from app.config import SettingsError
from app.infrastructure.database.base import Base, Branch
from app.infrastructure.database.branches import parse_branches, table_filter
from app.infrastructure.database.models import Base as AllModels


def test_tables_carry_their_branch() -> None:
    branches = {name: table.info["branch"] for name, table in AllModels.metadata.tables.items()}

    assert set(branches.values()) <= set(Branch)
    assert {name for name, branch in branches.items() if branch == Branch.PLATFORM} == {
        "file",
        "audit_logs",
    }


def test_model_without_branch_is_rejected() -> None:
    with pytest.raises(TypeError, match="__branch__"):

        class Orphan(Base):
            __tablename__ = "orphan_probe"
            __table_args__ = {"extend_existing": True}  # noqa: RUF012

            id: Mapped[int] = mapped_column(primary_key=True)


def test_parse_branches() -> None:
    assert parse_branches(None) == set(Branch)
    assert parse_branches("platform, modules") == {Branch.PLATFORM, Branch.MODULES}
    assert parse_branches("platform,") == {Branch.PLATFORM}
    with pytest.raises(ValueError, match="未知分支"):
        parse_branches("platform,notes")


def test_table_filter_skips_other_branches_and_unknown_tables() -> None:
    include = table_filter({Branch.PLATFORM})
    tables = AllModels.metadata.tables

    assert include(tables["file"], "file", "table", False, None)
    assert not include(tables["users"], "users", "table", False, None)
    assert not include(object(), "stray", "table", True, None)
    assert include(tables["file"], "file", "table", True, tables["file"])


def _alembic_config(output: io.StringIO | None = None) -> Config:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return Config(os.path.join(root, "alembic.ini"), output_buffer=output)


def test_offline_sql_creates_schema_and_sets_search_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_SCHEMA", "svc_a")
    output = io.StringIO()

    command.upgrade(_alembic_config(output), "heads", sql=True)

    sql = output.getvalue()
    schema_at = sql.index('CREATE SCHEMA IF NOT EXISTS "svc_a"')
    assert schema_at < sql.index('SET search_path TO "svc_a"') < sql.index("CREATE TABLE file")
    assert "CREATE TABLE svc_a.alembic_version" in sql


def test_invalid_database_url_error_hides_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://app:TopSecret@db/app")

    with pytest.raises(SettingsError) as caught:
        command.upgrade(_alembic_config(io.StringIO()), "heads", sql=True)

    assert "DATABASE_URL" in str(caught.value)
    assert "TopSecret" not in str(caught.value)
