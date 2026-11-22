"""identity initial tables

Revision ID: identity_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "identity_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("identity",)
depends_on: str | Sequence[str] | None = "platform_0001"


def upgrade() -> None:
    op.create_table(
        "permission",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("resource", sa.String(length=50), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_permission")),
    )
    op.create_index(op.f("ix_permission_resource"), "permission", ["resource"], unique=False)
    op.create_table(
        "role",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role")),
    )
    op.create_table(
        "status",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_status")),
    )
    op.create_table(
        "role_permission",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permission.id"],
            name=op.f("fk_role_permission_permission_id_permission"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name=op.f("fk_role_permission_role_id_role"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name=op.f("pk_role_permission")),
    )
    op.create_index(
        op.f("ix_role_permission_permission_id"), "role_permission", ["permission_id"], unique=False
    )
    op.create_index(
        op.f("ix_role_permission_role_id"), "role_permission", ["role_id"], unique=False
    )
    op.create_table(
        "user",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("password", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), server_default=sa.text("'email'"), nullable=False),
        sa.Column("first_name", sa.String(), nullable=True),
        sa.Column("last_name", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("photo_id", sa.Uuid(), nullable=True),
        sa.Column("role_id", sa.Integer(), nullable=True),
        sa.Column("status_id", sa.Integer(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("nickname", sa.String(), nullable=True),
        sa.Column("gender", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("birthday", sa.Date(), nullable=True),
        sa.Column("wechat_open_id", sa.String(), nullable=True),
        sa.Column("wechat_union_id", sa.String(), nullable=True),
        sa.Column("alipay_open_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["photo_id"], ["file.id"], name=op.f("fk_user_photo_id_file")),
        sa.ForeignKeyConstraint(["role_id"], ["role.id"], name=op.f("fk_user_role_id_role")),
        sa.ForeignKeyConstraint(
            ["status_id"], ["status.id"], name=op.f("fk_user_status_id_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("alipay_open_id", name=op.f("uq_user_alipay_open_id")),
        sa.UniqueConstraint("email", name=op.f("uq_user_email")),
        sa.UniqueConstraint("phone", name=op.f("uq_user_phone")),
        sa.UniqueConstraint("photo_id", name=op.f("uq_user_photo_id")),
        sa.UniqueConstraint("wechat_open_id", name=op.f("uq_user_wechat_open_id")),
    )
    op.create_index(
        "ix_user_active",
        "user",
        ["deleted_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(op.f("ix_user_first_name"), "user", ["first_name"], unique=False)
    op.create_index(op.f("ix_user_last_name"), "user", ["last_name"], unique=False)
    op.create_index(op.f("ix_user_wechat_union_id"), "user", ["wechat_union_id"], unique=False)
    op.create_table(
        "account_cleanup",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_account_cleanup_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_account_cleanup")),
    )
    op.create_table(
        "session",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name=op.f("fk_session_user_id_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session")),
    )
    op.create_index(
        "ix_session_active",
        "session",
        ["deleted_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(op.f("ix_session_user_id"), "session", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_session_user_id"), table_name="session")
    op.drop_index(
        "ix_session_active", table_name="session", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_table("session")
    op.drop_table("account_cleanup")
    op.drop_index(op.f("ix_user_wechat_union_id"), table_name="user")
    op.drop_index(op.f("ix_user_last_name"), table_name="user")
    op.drop_index(op.f("ix_user_first_name"), table_name="user")
    op.drop_index(
        "ix_user_active", table_name="user", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_table("user")
    op.drop_index(op.f("ix_role_permission_role_id"), table_name="role_permission")
    op.drop_index(op.f("ix_role_permission_permission_id"), table_name="role_permission")
    op.drop_table("role_permission")
    op.drop_table("status")
    op.drop_table("role")
    op.drop_index(op.f("ix_permission_resource"), table_name="permission")
    op.drop_table("permission")
