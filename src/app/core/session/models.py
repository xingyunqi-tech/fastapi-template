from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import (
    Base,
    Branch,
    created_at,
    timestamp,
    updated_at,
    uuid_pk,
)


class SessionModel(Base):
    __tablename__ = "session"
    __branch__ = Branch.IDENTITY

    id: Mapped[UUID] = uuid_pk()
    hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = timestamp(nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("user.id"), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip: Mapped[str | None] = mapped_column(String(64))
    last_active_at: Mapped[datetime | None] = timestamp(nullable=True)

    __table_args__ = (
        Index("ix_session_active", "deleted_at", postgresql_where=text("deleted_at IS NULL")),
    )
