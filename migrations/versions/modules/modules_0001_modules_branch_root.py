"""modules branch root

Revision ID: modules_0001
Revises:
"""

from collections.abc import Sequence

revision: str = "modules_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("modules",)
depends_on: str | Sequence[str] | None = "platform_0001"


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
