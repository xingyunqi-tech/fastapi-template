from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, Branch, created_at, updated_at, uuid_pk


class NoteStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class NoteModel(Base):
    __tablename__ = "notes"
    __branch__ = Branch.MODULES

    id: Mapped[UUID] = uuid_pk()
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'draft'"), default=NoteStatus.DRAFT
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (CheckConstraint("status IN ('draft', 'published')", name="status"),)
