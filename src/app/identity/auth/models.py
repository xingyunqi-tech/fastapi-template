from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, Branch, created_at, timestamp, uuid_pk


class TokenPurpose(StrEnum):
    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"  # noqa: S105


class VerificationTokenModel(Base):
    __tablename__ = "verification_tokens"
    __branch__ = Branch.IDENTITY

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_fingerprint: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = timestamp()
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        UniqueConstraint("user_id", "purpose"),
        CheckConstraint("purpose IN ('verify_email', 'reset_password')", name="purpose"),
    )
