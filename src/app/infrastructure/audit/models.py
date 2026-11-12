from datetime import datetime
from typing import Any

from sqlalchemy import Index, Integer, PrimaryKeyConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, created_at


class AuditLogModel(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str | None] = mapped_column("userId", String)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column("entityType", String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column("entityId", String, nullable=False)
    old_value: Mapped[Any | None] = mapped_column("oldValue", JSONB)
    new_value: Mapped[Any | None] = mapped_column("newValue", JSONB)
    ip_address: Mapped[str | None] = mapped_column("ipAddress", String)
    user_agent: Mapped[str | None] = mapped_column("userAgent", Text)
    request_id: Mapped[str | None] = mapped_column("requestId", String)
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_audit_logs"),
        Index("IDX_audit_logs_action", "action"),
        Index("IDX_audit_logs_createdAt", "createdAt"),
        Index("IDX_audit_logs_entityId", "entityId"),
        Index("IDX_audit_logs_entityType", "entityType"),
        Index("IDX_audit_logs_requestId", "requestId"),
        Index("IDX_audit_logs_userId", "userId"),
    )
