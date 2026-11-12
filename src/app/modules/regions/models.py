from datetime import datetime

from sqlalchemy import Index, Integer, PrimaryKeyConstraint, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, created_at, updated_at


class RegionModel(Base):
    __tablename__ = "region"

    id: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_code: Mapped[str | None] = mapped_column("parentCode", String(12))
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_region_id"),
        UniqueConstraint("code", name="UQ_region_code"),
        Index("IDX_region_code", "code"),
        Index("IDX_region_level", "level"),
        Index("IDX_region_level_parentCode", "level", "parentCode"),
        Index("IDX_region_parentCode", "parentCode"),
        {"comment": "Administrative division data (Province/City/District)"},
    )
