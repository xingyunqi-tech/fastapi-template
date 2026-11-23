from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import (
    Base,
    Branch,
    created_at,
    timestamp,
    updated_at,
    uuid_pk,
)
from app.modules.files.models import FileModel


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class UserRoleModel(Base):
    __tablename__ = "user_roles"
    __branch__ = Branch.IDENTITY

    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), primary_key=True)


class UserModel(Base):
    __tablename__ = "users"
    __branch__ = Branch.IDENTITY

    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str | None] = mapped_column(String(254))
    email_verified_at: Mapped[datetime | None] = timestamp(nullable=True)
    phone: Mapped[str | None] = mapped_column(String(16))
    password_hash: Mapped[str | None] = mapped_column(String)
    display_name: Mapped[str | None] = mapped_column(String(64))
    avatar_file_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("files.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    last_login_at: Mapped[datetime | None] = timestamp(nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = timestamp(nullable=True)

    avatar: Mapped[FileModel | None] = relationship(lazy="raise")
    role_rows: Mapped[list[UserRoleModel]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="status"),
        Index("uq_users_email", "email", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("uq_users_phone", "phone", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_users_created_at", "created_at"),
    )

    @property
    def roles(self) -> list[str]:
        return sorted(row.role for row in self.role_rows)
