from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, Branch, created_at, uuid_pk


class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __branch__ = Branch.PLATFORM

    id: Mapped[UUID] = uuid_pk()
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(128))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    changes: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (Index(None, "created_at"),)
