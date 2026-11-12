import os
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import get_settings
from tests.integration.conftest import ROOT, PgServer, clone_database

SNAPSHOT = Path(ROOT) / "db" / "nestjs-schema.sql"
TABLES = [
    "user",
    "role",
    "status",
    "session",
    "permission",
    "role_permission",
    "file",
    "audit_logs",
    "config",
    "region",
    "notification",
    "notification_setting",
    "address",
    "favorite",
    "feedback",
    "user_consent",
    "account_cleanup",
    "admin_alert",
]
EXPECTED_MISSING = {
    'constraint feedback.FK_feedback_errata FOREIGN KEY ("errataId") REFERENCES errata(id) '
    "ON DELETE SET NULL",
    'constraint feedback.FK_feedback_question FOREIGN KEY ("questionId") REFERENCES question(id) '
    "ON DELETE SET NULL",
}


def _snapshot(dsn: str) -> list[str]:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(Path(ROOT) / "scripts" / "schema_snapshot.py"), dsn, *TABLES],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def test_migrated_schema_matches_nestjs(pg_server: PgServer, template_db: str) -> None:
    expected = set(SNAPSHOT.read_text(encoding="utf-8").splitlines())
    actual = set(_snapshot(pg_server.dsn(template_db)))

    assert expected - actual == EXPECTED_MISSING
    assert actual - expected == set()


def _alembic(pg_server: PgServer, database: str) -> Config:
    os.environ["DATABASE_URL"] = pg_server.url(database)
    get_settings.cache_clear()
    return Config(os.path.join(ROOT, "alembic.ini"))


def test_models_match_migrations(pg_server: PgServer, template_db: str) -> None:
    command.check(_alembic(pg_server, template_db))


def test_downgrade_then_upgrade(pg_server: PgServer, template_db: str) -> None:
    name = clone_database(pg_server, "migration_roundtrip")
    config = _alembic(pg_server, name)

    command.downgrade(config, "base")
    command.upgrade(config, "head")

    assert set(_snapshot(pg_server.dsn(name))) == set(_snapshot(pg_server.dsn(template_db)))
