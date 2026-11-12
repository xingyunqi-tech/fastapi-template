from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, PrimaryKeyConstraint, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, created_at, updated_at


class FeedbackModel(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int | None] = mapped_column(
        "userId", ForeignKey("user.id", name="FK_feedback_user", ondelete="SET NULL")
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    images: Mapped[Any | None] = mapped_column(JSONB)
    contact: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    reply: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    question_id: Mapped[UUID | None] = mapped_column("questionId")
    question_version: Mapped[int | None] = mapped_column("questionVersion", Integer)
    reason: Mapped[str | None] = mapped_column(String(20))
    errata_id: Mapped[UUID | None] = mapped_column("errataId")
    message_id: Mapped[UUID | None] = mapped_column("messageId")
    message_snapshot: Mapped[Any | None] = mapped_column("messageSnapshot", JSONB)

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_feedback_id"),
        Index("IDX_feedback_errataId", "errataId"),
        Index("IDX_feedback_questionId", "questionId"),
        Index("IDX_feedback_status", "status"),
        Index("IDX_feedback_type", "type"),
        Index("IDX_feedback_userId", "userId"),
    )
