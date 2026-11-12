from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, created_at, timestamp, updated_at


class NotificationModel(Base):
    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(
        "userId",
        ForeignKey("user.id", name="FK_notification_user", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[Any | None] = mapped_column("metadata", JSONB)
    channels: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    sent_channels: Mapped[Any] = mapped_column(
        "sentChannels", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_read: Mapped[bool] = mapped_column(
        "isRead", Boolean, nullable=False, server_default=text("false")
    )
    read_at: Mapped[datetime | None] = timestamp("readAt", nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = timestamp("deletedAt", nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_notification_id"),
        Index("IDX_notification_category", "category"),
        Index("IDX_notification_createdAt", "createdAt"),
        Index("IDX_notification_isRead", "isRead"),
        Index("IDX_notification_userId", "userId"),
    )


class NotificationSettingModel(Base):
    __tablename__ = "notification_setting"

    id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(
        "userId",
        ForeignKey("user.id", name="FK_notification_setting_user", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    enable_in_app: Mapped[bool] = mapped_column(
        "enableInApp", Boolean, nullable=False, server_default=text("true")
    )
    enable_email: Mapped[bool] = mapped_column(
        "enableEmail", Boolean, nullable=False, server_default=text("true")
    )
    enable_sms: Mapped[bool] = mapped_column(
        "enableSms", Boolean, nullable=False, server_default=text("true")
    )
    enable_push: Mapped[bool] = mapped_column(
        "enablePush", Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_notification_setting_id"),
        UniqueConstraint("userId", "category", name="UQ_notification_setting_userId_category"),
        Index("IDX_notification_setting_userId", "userId"),
    )
