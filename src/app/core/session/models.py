from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, PrimaryKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, created_at, timestamp, updated_at


class SessionModel(Base):
    __tablename__ = "session"

    id: Mapped[int] = mapped_column(Integer)
    hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = timestamp("deletedAt", nullable=True)
    user_id: Mapped[int | None] = mapped_column(
        "userId", ForeignKey("user.id", name="FK_3d2f174ef04fb312fdebd0ddc53")
    )
    user_agent: Mapped[str | None] = mapped_column("userAgent", String(512))
    ip: Mapped[str | None] = mapped_column(String(64))
    last_active_at: Mapped[datetime | None] = timestamp("lastActiveAt", nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_f55da76ac1c3ac420f444d2ff11"),
        Index("IDX_3d2f174ef04fb312fdebd0ddc5", "userId"),
        Index("IDX_session_deletedAt", "deletedAt", postgresql_where=text('"deletedAt" IS NULL')),
    )
