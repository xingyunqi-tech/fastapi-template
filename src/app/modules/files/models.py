from uuid import UUID

from sqlalchemy import Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, Branch, uuid_pk


class FileModel(Base):
    __tablename__ = "file"
    __branch__ = Branch.PLATFORM

    id: Mapped[UUID] = uuid_pk()
    path: Mapped[str] = mapped_column(String, nullable=False, index=True)
    owner_user_id: Mapped[UUID | None] = mapped_column(Uuid, index=True)
    visibility: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'PUBLIC'")
    )
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
