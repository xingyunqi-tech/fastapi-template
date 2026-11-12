from uuid import UUID

from sqlalchemy import Index, Integer, PrimaryKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class FileModel(Base):
    __tablename__ = "file"

    id: Mapped[UUID] = mapped_column(server_default=text("uuid_generate_v4()"))
    path: Mapped[str] = mapped_column(String, nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column("ownerUserId", Integer)
    visibility: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'PUBLIC'")
    )
    mime_type: Mapped[str | None] = mapped_column("mimeType", String(100))
    size: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_36b46d232307066b3a2c9ea3a1d"),
        Index("IDX_file_ownerUserId", "ownerUserId"),
        Index("IDX_file_path", "path"),
        Index("IDX_file_sha256", "sha256"),
    )
