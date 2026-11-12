from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class AccountCleanupModel(Base):
    __tablename__ = "account_cleanup"

    user_id: Mapped[int] = mapped_column(
        "userId",
        Integer,
        ForeignKey("user.id", name="FK_account_cleanup_user", ondelete="CASCADE"),
        autoincrement=False,
    )
    completed_at: Mapped[datetime] = mapped_column(
        "completedAt", DateTime(timezone=True), nullable=False
    )

    __table_args__ = (PrimaryKeyConstraint("userId", name="PK_account_cleanup"),)
