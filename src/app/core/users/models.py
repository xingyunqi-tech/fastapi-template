from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Uuid, text
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


class RoleModel(Base):
    __tablename__ = "role"
    __branch__ = Branch.IDENTITY

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String, nullable=False)


class StatusModel(Base):
    __tablename__ = "status"
    __branch__ = Branch.IDENTITY

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String, nullable=False)


class UserModel(Base):
    __tablename__ = "user"
    __branch__ = Branch.IDENTITY

    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str | None] = mapped_column(String, unique=True)
    password: Mapped[str | None] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'email'"))
    first_name: Mapped[str | None] = mapped_column(String, index=True)
    last_name: Mapped[str | None] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = timestamp(nullable=True)
    photo_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("file.id"), unique=True)
    role_id: Mapped[int | None] = mapped_column(ForeignKey("role.id"))
    status_id: Mapped[int | None] = mapped_column(ForeignKey("status.id"))
    phone: Mapped[str | None] = mapped_column(String, unique=True)
    nickname: Mapped[str | None] = mapped_column(String)
    gender: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    birthday: Mapped[date | None] = mapped_column(Date)
    wechat_open_id: Mapped[str | None] = mapped_column(String, unique=True)
    wechat_union_id: Mapped[str | None] = mapped_column(String, index=True)
    alipay_open_id: Mapped[str | None] = mapped_column(String, unique=True)

    photo: Mapped[FileModel | None] = relationship(lazy="raise")
    role: Mapped[RoleModel | None] = relationship(lazy="raise")
    status: Mapped[StatusModel | None] = relationship(lazy="raise")

    __table_args__ = (
        Index("ix_user_active", "deleted_at", postgresql_where=text("deleted_at IS NULL")),
    )
