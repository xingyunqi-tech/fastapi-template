from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, PrimaryKeyConstraint, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, created_at, updated_at


class ConfigModel(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(200))
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    is_public: Mapped[bool] = mapped_column(
        "isPublic", Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (PrimaryKeyConstraint("key", name="PK_config_key"),)
