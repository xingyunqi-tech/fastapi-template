from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, created_at, updated_at


class PermissionModel(Base):
    __tablename__ = "permission"

    id: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    resource: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_permission_id"),
        Index("IDX_PERMISSION_RESOURCE", "resource"),
    )


class RolePermissionModel(Base):
    __tablename__ = "role_permission"

    role_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("role.id", name="FK_ROLE_PERMISSION_ROLE", ondelete="CASCADE"),
    )
    permission_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("permission.id", name="FK_ROLE_PERMISSION_PERMISSION", ondelete="CASCADE"),
    )
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        PrimaryKeyConstraint("role_id", "permission_id", name="PK_role_permission"),
        Index("IDX_ROLE_PERMISSION_PERMISSION", "permission_id"),
        Index("IDX_ROLE_PERMISSION_ROLE", "role_id"),
    )
