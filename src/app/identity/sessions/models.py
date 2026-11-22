from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, LargeBinary, String, Uuid
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, Branch, created_at, timestamp, uuid_pk


class SessionModel(Base):
    __tablename__ = "sessions"
    __branch__ = Branch.IDENTITY

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip: Mapped[str | None] = mapped_column(INET)
    auth_time: Mapped[datetime] = timestamp()
    created_at: Mapped[datetime] = created_at()
    last_active_at: Mapped[datetime | None] = timestamp(nullable=True)
    expires_at: Mapped[datetime] = timestamp()
    revoked_at: Mapped[datetime | None] = timestamp(nullable=True)


class RefreshTokenModel(Base):
    __tablename__ = "refresh_tokens"
    __branch__ = Branch.IDENTITY

    id: Mapped[UUID] = uuid_pk()
    session_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at()
    rotated_at: Mapped[datetime | None] = timestamp(nullable=True)
    grace_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
