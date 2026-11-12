from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class UserConsentModel(Base):
    __tablename__ = "user_consent"

    id: Mapped[UUID] = mapped_column(server_default=text("uuid_generate_v4()"))
    user_id: Mapped[int] = mapped_column(
        "userId",
        Integer,
        ForeignKey("user.id", name="FK_user_consent_user", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    agreed_at: Mapped[datetime] = mapped_column(
        "agreedAt", DateTime(), nullable=False, server_default=func.now()
    )
    ip: Mapped[str | None] = mapped_column(String(45))

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_user_consent"),
        Index("UQ_user_consent_userId_type_version", "userId", "type", "version", unique=True),
    )
