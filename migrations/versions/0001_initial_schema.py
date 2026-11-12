"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-11 18:52:01.409851
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
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
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("userId", sa.String(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("entityType", sa.String(length=100), nullable=False),
        sa.Column("entityId", sa.String(), nullable=False),
        sa.Column("oldValue", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("newValue", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ipAddress", sa.String(), nullable=True),
        sa.Column("userAgent", sa.Text(), nullable=True),
        sa.Column("requestId", sa.String(), nullable=True),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="PK_audit_logs"),
    )
    op.create_index("IDX_audit_logs_action", "audit_logs", ["action"], unique=False)
    op.create_index("IDX_audit_logs_createdAt", "audit_logs", ["createdAt"], unique=False)
    op.create_index("IDX_audit_logs_entityId", "audit_logs", ["entityId"], unique=False)
    op.create_index("IDX_audit_logs_entityType", "audit_logs", ["entityType"], unique=False)
    op.create_index("IDX_audit_logs_requestId", "audit_logs", ["requestId"], unique=False)
    op.create_index("IDX_audit_logs_userId", "audit_logs", ["userId"], unique=False)
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
        "file",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("ownerUserId", sa.Integer(), nullable=True),
        sa.Column(
            "visibility", sa.String(length=10), server_default=sa.text("'PUBLIC'"), nullable=False
        ),
        sa.Column("mimeType", sa.String(length=100), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="PK_36b46d232307066b3a2c9ea3a1d"),
    )
    op.create_index("IDX_file_ownerUserId", "file", ["ownerUserId"], unique=False)
    op.create_index("IDX_file_path", "file", ["path"], unique=False)
    op.create_index("IDX_file_sha256", "file", ["sha256"], unique=False)
    op.create_table(
        "permission",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("resource", sa.String(length=50), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="PK_permission_id"),
    )
    op.create_index("IDX_PERMISSION_RESOURCE", "permission", ["resource"], unique=False)
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
        "role",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="PK_b36bcfe02fc8de3c57a8b2391c2"),
    )
    op.create_table(
        "status",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="PK_e12743a7086ec826733f54e1d95"),
    )
    op.create_table(
        "role_permission",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.String(length=50), nullable=False),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permission.id"],
            name="FK_ROLE_PERMISSION_PERMISSION",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["role.id"], name="FK_ROLE_PERMISSION_ROLE", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name="PK_role_permission"),
    )
    op.create_index(
        "IDX_ROLE_PERMISSION_PERMISSION", "role_permission", ["permission_id"], unique=False
    )
    op.create_index("IDX_ROLE_PERMISSION_ROLE", "role_permission", ["role_id"], unique=False)
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("password", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), server_default=sa.text("'email'"), nullable=False),
        sa.Column("firstName", sa.String(), nullable=True),
        sa.Column("lastName", sa.String(), nullable=True),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deletedAt", sa.DateTime(), nullable=True),
        sa.Column("photoId", sa.Uuid(), nullable=True),
        sa.Column("roleId", sa.Integer(), nullable=True),
        sa.Column("statusId", sa.Integer(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("nickname", sa.String(), nullable=True),
        sa.Column("gender", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("birthday", sa.Date(), nullable=True),
        sa.Column("wechatOpenId", sa.String(), nullable=True),
        sa.Column("wechatUnionId", sa.String(), nullable=True),
        sa.Column("alipayOpenId", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["photoId"], ["file.id"], name="FK_75e2be4ce11d447ef43be0e374f"),
        sa.ForeignKeyConstraint(["roleId"], ["role.id"], name="FK_c28e52f758e7bbc53828db92194"),
        sa.ForeignKeyConstraint(["statusId"], ["status.id"], name="FK_dc18daa696860586ba4667a9d31"),
        sa.PrimaryKeyConstraint("id", name="PK_cace4a159ff9f2512dd42373760"),
        sa.UniqueConstraint("alipayOpenId", name="user_alipayOpenId_key"),
        sa.UniqueConstraint("email", name="UQ_e12875dfb3b1d92d7d7c5377e22"),
        sa.UniqueConstraint("phone", name="user_phone_key"),
        sa.UniqueConstraint("photoId", name="REL_75e2be4ce11d447ef43be0e374"),
        sa.UniqueConstraint("wechatOpenId", name="user_wechatOpenId_key"),
    )
    op.create_index("IDX_58e4dbff0e1a32a9bdc861bb29", "user", ["firstName"], unique=False)
    op.create_index("IDX_f0e1b4ecdca13b177e2e3a0613", "user", ["lastName"], unique=False)
    op.create_index(
        "IDX_user_deletedAt",
        "user",
        ["deletedAt"],
        unique=False,
        postgresql_where=sa.text('"deletedAt" IS NULL'),
    )
    op.create_index("IDX_user_phone", "user", ["phone"], unique=False)
    op.create_index("IDX_user_wechatOpenId", "user", ["wechatOpenId"], unique=False)
    op.create_index("IDX_user_wechatUnionId", "user", ["wechatUnionId"], unique=False)
    op.create_table(
        "account_cleanup",
        sa.Column("userId", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("completedAt", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["userId"], ["user.id"], name="FK_account_cleanup_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("userId", name="PK_account_cleanup"),
    )
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
        "session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hash", sa.String(), nullable=False),
        sa.Column("createdAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updatedAt", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("deletedAt", sa.DateTime(), nullable=True),
        sa.Column("userId", sa.Integer(), nullable=True),
        sa.Column("userAgent", sa.String(length=512), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("lastActiveAt", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["userId"], ["user.id"], name="FK_3d2f174ef04fb312fdebd0ddc53"),
        sa.PrimaryKeyConstraint("id", name="PK_f55da76ac1c3ac420f444d2ff11"),
    )
    op.create_index("IDX_3d2f174ef04fb312fdebd0ddc5", "session", ["userId"], unique=False)
    op.create_index(
        "IDX_session_deletedAt",
        "session",
        ["deletedAt"],
        unique=False,
        postgresql_where=sa.text('"deletedAt" IS NULL'),
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


def downgrade() -> None:
    op.drop_index("UQ_user_consent_userId_type_version", table_name="user_consent")
    op.drop_table("user_consent")
    op.drop_index(
        "IDX_session_deletedAt",
        table_name="session",
        postgresql_where=sa.text('"deletedAt" IS NULL'),
    )
    op.drop_index("IDX_3d2f174ef04fb312fdebd0ddc5", table_name="session")
    op.drop_table("session")
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
    op.drop_table("account_cleanup")
    op.drop_index("IDX_user_wechatUnionId", table_name="user")
    op.drop_index("IDX_user_wechatOpenId", table_name="user")
    op.drop_index("IDX_user_phone", table_name="user")
    op.drop_index(
        "IDX_user_deletedAt", table_name="user", postgresql_where=sa.text('"deletedAt" IS NULL')
    )
    op.drop_index("IDX_f0e1b4ecdca13b177e2e3a0613", table_name="user")
    op.drop_index("IDX_58e4dbff0e1a32a9bdc861bb29", table_name="user")
    op.drop_table("user")
    op.drop_index("IDX_ROLE_PERMISSION_ROLE", table_name="role_permission")
    op.drop_index("IDX_ROLE_PERMISSION_PERMISSION", table_name="role_permission")
    op.drop_table("role_permission")
    op.drop_table("status")
    op.drop_table("role")
    op.drop_index("IDX_region_parentCode", table_name="region")
    op.drop_index("IDX_region_level_parentCode", table_name="region")
    op.drop_index("IDX_region_level", table_name="region")
    op.drop_index("IDX_region_code", table_name="region")
    op.drop_table("region")
    op.drop_index("IDX_PERMISSION_RESOURCE", table_name="permission")
    op.drop_table("permission")
    op.drop_index("IDX_file_sha256", table_name="file")
    op.drop_index("IDX_file_path", table_name="file")
    op.drop_index("IDX_file_ownerUserId", table_name="file")
    op.drop_table("file")
    op.drop_table("config")
    op.drop_index("IDX_audit_logs_userId", table_name="audit_logs")
    op.drop_index("IDX_audit_logs_requestId", table_name="audit_logs")
    op.drop_index("IDX_audit_logs_entityType", table_name="audit_logs")
    op.drop_index("IDX_audit_logs_entityId", table_name="audit_logs")
    op.drop_index("IDX_audit_logs_createdAt", table_name="audit_logs")
    op.drop_index("IDX_audit_logs_action", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(
        "UQ_admin_alert_dedupKey_open",
        table_name="admin_alert",
        postgresql_where=sa.text('"acknowledgedAt" IS NULL'),
    )
    op.drop_index("IDX_admin_alert_type", table_name="admin_alert")
    op.drop_index("IDX_admin_alert_createdAt", table_name="admin_alert")
    op.drop_table("admin_alert")
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
