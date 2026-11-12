from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, created_at


class FavoriteModel(Base):
    __tablename__ = "favorite"

    id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(
        "userId", ForeignKey("user.id", name="FK_favorite_user", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column("targetType", String(20), nullable=False)
    target_id: Mapped[str] = mapped_column("targetId", String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    image: Mapped[str | None] = mapped_column(String(500))
    extra: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    subject_id: Mapped[UUID | None] = mapped_column("subjectId")

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_favorite_id"),
        UniqueConstraint("userId", "targetType", "targetId", name="UQ_favorite_user_target"),
        Index("IDX_favorite_targetId", "targetId"),
        Index("IDX_favorite_targetType", "targetType"),
        Index("IDX_favorite_userId", "userId"),
        Index("IDX_favorite_userId_subjectId", "userId", "subjectId"),
        Index("IDX_favorite_userId_targetType", "userId", "targetType"),
    )
