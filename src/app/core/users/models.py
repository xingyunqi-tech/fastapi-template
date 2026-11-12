from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, created_at, timestamp, updated_at
from app.modules.files.models import FileModel


class RoleModel(Base):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (PrimaryKeyConstraint("id", name="PK_b36bcfe02fc8de3c57a8b2391c2"),)


class StatusModel(Base):
    __tablename__ = "status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (PrimaryKeyConstraint("id", name="PK_e12743a7086ec826733f54e1d95"),)


class UserModel(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str | None] = mapped_column(String)
    password: Mapped[str | None] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'email'"))
    first_name: Mapped[str | None] = mapped_column("firstName", String)
    last_name: Mapped[str | None] = mapped_column("lastName", String)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = timestamp("deletedAt", nullable=True)
    photo_id: Mapped[UUID | None] = mapped_column(
        "photoId",
        ForeignKey("file.id", name="FK_75e2be4ce11d447ef43be0e374f"),
    )
    role_id: Mapped[int | None] = mapped_column(
        "roleId", ForeignKey("role.id", name="FK_c28e52f758e7bbc53828db92194")
    )
    status_id: Mapped[int | None] = mapped_column(
        "statusId", ForeignKey("status.id", name="FK_dc18daa696860586ba4667a9d31")
    )
    phone: Mapped[str | None] = mapped_column(String)
    nickname: Mapped[str | None] = mapped_column(String)
    gender: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    birthday: Mapped[date | None] = mapped_column(Date)
    wechat_open_id: Mapped[str | None] = mapped_column("wechatOpenId", String)
    wechat_union_id: Mapped[str | None] = mapped_column("wechatUnionId", String)
    alipay_open_id: Mapped[str | None] = mapped_column("alipayOpenId", String)

    photo: Mapped[FileModel | None] = relationship(lazy="raise")
    role: Mapped[RoleModel | None] = relationship(lazy="raise")
    status: Mapped[StatusModel | None] = relationship(lazy="raise")

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_cace4a159ff9f2512dd42373760"),
        UniqueConstraint("photoId", name="REL_75e2be4ce11d447ef43be0e374"),
        UniqueConstraint("email", name="UQ_e12875dfb3b1d92d7d7c5377e22"),
        UniqueConstraint("alipayOpenId", name="user_alipayOpenId_key"),
        UniqueConstraint("phone", name="user_phone_key"),
        UniqueConstraint("wechatOpenId", name="user_wechatOpenId_key"),
        Index("IDX_58e4dbff0e1a32a9bdc861bb29", "firstName"),
        Index("IDX_f0e1b4ecdca13b177e2e3a0613", "lastName"),
        Index(
            "IDX_user_deletedAt",
            "deletedAt",
            postgresql_where=text('"deletedAt" IS NULL'),
        ),
        Index("IDX_user_phone", "phone"),
        Index("IDX_user_wechatOpenId", "wechatOpenId"),
        Index("IDX_user_wechatUnionId", "wechatUnionId"),
    )
