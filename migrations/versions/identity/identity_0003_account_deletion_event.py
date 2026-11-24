"""account deletion event published at

Revision ID: identity_0003
Revises: identity_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "identity_0003"
down_revision: str | Sequence[str] | None = "identity_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "account_deletions",
        sa.Column("event_published_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("account_deletions", "event_published_at")
