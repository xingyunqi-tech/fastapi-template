from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, Branch, created_at, uuid_pk


class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __branch__ = Branch.PLATFORM

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[str | None] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    old_value: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    new_value: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    ip_address: Mapped[str | None] = mapped_column(String)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (Index(None, "created_at"),)
