"""drop business tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("UQ_user_consent_userId_type_version", table_name="user_consent")
    op.drop_table("user_consent")
    op.drop_index("IDX_notification_setting_userId", table_name="notification_setting")
    op.drop_table("notification_setting")
    op.drop_index("IDX_notification_userId", table_name="notification")
    op.drop_index("IDX_notification_isRead", table_name="notification")
    op.drop_index("IDX_notification_createdAt", table_name="notification")
    op.drop_index("IDX_notification_category", table_name="notification")
    op.drop_table("notification")
    op.drop_index("IDX_feedback_userId", table_name="feedback")
    op.drop_index("IDX_feedback_type", table_name="feedback")
    op.drop_index("IDX_feedback_status", table_name="feedback")
    op.drop_index("IDX_feedback_questionId", table_name="feedback")
    op.drop_index("IDX_feedback_errataId", table_name="feedback")
    op.drop_table("feedback")
    op.drop_index("IDX_favorite_userId_targetType", table_name="favorite")
    op.drop_index("IDX_favorite_userId_subjectId", table_name="favorite")
    op.drop_index("IDX_favorite_userId", table_name="favorite")
    op.drop_index("IDX_favorite_targetType", table_name="favorite")
    op.drop_index("IDX_favorite_targetId", table_name="favorite")
    op.drop_table("favorite")
    op.drop_index(
        "UQ_address_userId_default",
        table_name="address",
        postgresql_where=sa.text('"isDefault" AND ("deletedAt" IS NULL)'),
    )
    op.drop_index("IDX_address_userId_isDefault_createdAt", table_name="address")
    op.drop_index("IDX_address_userId", table_name="address")
    op.drop_index("IDX_address_provinceCode", table_name="address")
    op.drop_index("IDX_address_districtCode", table_name="address")
    op.drop_index(
        "IDX_address_deletedAt",
        table_name="address",
        postgresql_where=sa.text('"deletedAt" IS NULL'),
    )
    op.drop_index("IDX_address_cityCode", table_name="address")
    op.drop_table("address")
    op.drop_index("IDX_region_parentCode", table_name="region")
    op.drop_index("IDX_region_level_parentCode", table_name="region")
    op.drop_index("IDX_region_level", table_name="region")
    op.drop_index("IDX_region_code", table_name="region")
    op.drop_table("region")
    op.drop_table("config")
    op.drop_index(
        "UQ_admin_alert_dedupKey_open",
        table_name="admin_alert",
        postgresql_where=sa.text('"acknowledgedAt" IS NULL'),
    )
    op.drop_index("IDX_admin_alert_type", table_name="admin_alert")
    op.drop_index("IDX_admin_alert_createdAt", table_name="admin_alert")
    op.drop_table("admin_alert")


def downgrade() -> None:
    op.create_table(
        "admin_alert",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dedupKey", sa.String(length=200), nullable=False),
        sa.Column("acknowledgedAt", sa.DateTime(), nullable=True),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="PK_44cf0b17edf69fb3f9b62c1ee7b"),
    )
    op.create_index("IDX_admin_alert_createdAt", "admin_alert", ["createdAt"], unique=False)
    op.create_index("IDX_admin_alert_type", "admin_alert", ["type"], unique=False)
    op.create_index(
        "UQ_admin_alert_dedupKey_open",
        "admin_alert",
        ["dedupKey"],
        unique=True,
        postgresql_where=sa.text('"acknowledgedAt" IS NULL'),
    )
    op.create_table(
        "config",
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("isPublic", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("key", name="PK_config_key"),
    )
    op.create_table(
        "region",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("parentCode", sa.String(length=12), nullable=True),
        sa.Column("sort", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="PK_region_id"),
        sa.UniqueConstraint("code", name="UQ_region_code"),
        comment="Administrative division data (Province/City/District)",
    )
    op.create_index("IDX_region_code", "region", ["code"], unique=False)
    op.create_index("IDX_region_level", "region", ["level"], unique=False)
    op.create_index("IDX_region_level_parentCode", "region", ["level", "parentCode"], unique=False)
    op.create_index("IDX_region_parentCode", "region", ["parentCode"], unique=False)
    op.create_table(
        "address",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("userId", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("province", sa.String(length=50), nullable=False),
        sa.Column("city", sa.String(length=50), nullable=False),
        sa.Column("district", sa.String(length=50), nullable=False),
        sa.Column("address", sa.String(length=200), nullable=False),
        sa.Column("isDefault", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deletedAt", sa.DateTime(), nullable=True),
        sa.Column(
            "provinceCode", sa.String(length=12), nullable=True, comment="Province region code"
        ),
        sa.Column("cityCode", sa.String(length=12), nullable=True, comment="City region code"),
        sa.Column(
            "districtCode", sa.String(length=12), nullable=True, comment="District region code"
        ),
        sa.ForeignKeyConstraint(
            ["userId"], ["user.id"], name="FK_address_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="PK_address_id"),
    )
    op.create_index("IDX_address_cityCode", "address", ["cityCode"], unique=False)
    op.create_index(
        "IDX_address_deletedAt",
        "address",
        ["deletedAt"],
        unique=False,
        postgresql_where=sa.text('"deletedAt" IS NULL'),
    )
    op.create_index("IDX_address_districtCode", "address", ["districtCode"], unique=False)
    op.create_index("IDX_address_provinceCode", "address", ["provinceCode"], unique=False)
    op.create_index("IDX_address_userId", "address", ["userId"], unique=False)
    op.create_index(
        "IDX_address_userId_isDefault_createdAt",
        "address",
        ["userId", sa.literal_column('"isDefault" DESC'), sa.literal_column('"createdAt" DESC')],
        unique=False,
    )
    op.create_index(
        "UQ_address_userId_default",
        "address",
        ["userId"],
        unique=True,
        postgresql_where=sa.text('"isDefault" AND ("deletedAt" IS NULL)'),
    )
    op.create_table(
        "favorite",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("userId", sa.Integer(), nullable=False),
        sa.Column("targetType", sa.String(length=20), nullable=False),
        sa.Column("targetId", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("image", sa.String(length=500), nullable=True),
        sa.Column("extra", sa.Text(), nullable=True),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("subjectId", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["userId"], ["user.id"], name="FK_favorite_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="PK_favorite_id"),
        sa.UniqueConstraint("userId", "targetType", "targetId", name="UQ_favorite_user_target"),
    )
    op.create_index("IDX_favorite_targetId", "favorite", ["targetId"], unique=False)
    op.create_index("IDX_favorite_targetType", "favorite", ["targetType"], unique=False)
    op.create_index("IDX_favorite_userId", "favorite", ["userId"], unique=False)
    op.create_index(
        "IDX_favorite_userId_subjectId", "favorite", ["userId", "subjectId"], unique=False
    )
    op.create_index(
        "IDX_favorite_userId_targetType", "favorite", ["userId", "targetType"], unique=False
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("userId", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("images", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("contact", sa.String(length=100), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("questionId", sa.Uuid(), nullable=True),
        sa.Column("questionVersion", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=20), nullable=True),
        sa.Column("errataId", sa.Uuid(), nullable=True),
        sa.Column("messageId", sa.Uuid(), nullable=True),
        sa.Column("messageSnapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["userId"], ["user.id"], name="FK_feedback_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="PK_feedback_id"),
    )
    op.create_index("IDX_feedback_errataId", "feedback", ["errataId"], unique=False)
    op.create_index("IDX_feedback_questionId", "feedback", ["questionId"], unique=False)
    op.create_index("IDX_feedback_status", "feedback", ["status"], unique=False)
    op.create_index("IDX_feedback_type", "feedback", ["type"], unique=False)
    op.create_index("IDX_feedback_userId", "feedback", ["userId"], unique=False)
    op.create_table(
        "notification",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("userId", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "channels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "sentChannels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("isRead", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("readAt", sa.DateTime(), nullable=True),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deletedAt", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["userId"], ["user.id"], name="FK_notification_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="PK_notification_id"),
    )
    op.create_index("IDX_notification_category", "notification", ["category"], unique=False)
    op.create_index("IDX_notification_createdAt", "notification", ["createdAt"], unique=False)
    op.create_index("IDX_notification_isRead", "notification", ["isRead"], unique=False)
    op.create_index("IDX_notification_userId", "notification", ["userId"], unique=False)
    op.create_table(
        "notification_setting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("userId", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("enableInApp", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("enableEmail", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("enableSms", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("enablePush", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["userId"], ["user.id"], name="FK_notification_setting_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="PK_notification_setting_id"),
        sa.UniqueConstraint("userId", "category", name="UQ_notification_setting_userId_category"),
    )
    op.create_index(
        "IDX_notification_setting_userId", "notification_setting", ["userId"], unique=False
    )
    op.create_table(
        "user_consent",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("userId", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("agreedAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.ForeignKeyConstraint(
            ["userId"], ["user.id"], name="FK_user_consent_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="PK_user_consent"),
    )
    op.create_index(
        "UQ_user_consent_userId_type_version",
        "user_consent",
        ["userId", "type", "version"],
        unique=True,
    )
