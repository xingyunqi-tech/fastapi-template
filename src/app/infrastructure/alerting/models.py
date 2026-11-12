from datetime import datetime
from typing import Any

from sqlalchemy import Index, Integer, PrimaryKeyConstraint, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, created_at, timestamp, updated_at


class AdminAlertModel(Base):
    __tablename__ = "admin_alert"

    id: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    dedup_key: Mapped[str] = mapped_column("dedupKey", String(200), nullable=False)
    acknowledged_at: Mapped[datetime | None] = timestamp("acknowledgedAt", nullable=True)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_44cf0b17edf69fb3f9b62c1ee7b"),
        Index("IDX_admin_alert_createdAt", "createdAt"),
        Index("IDX_admin_alert_type", "type"),
        Index(
            "UQ_admin_alert_dedupKey_open",
            "dedupKey",
            unique=True,
            postgresql_where=text('"acknowledgedAt" IS NULL'),
        ),
    )
