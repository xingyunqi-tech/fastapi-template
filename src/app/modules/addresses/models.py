from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, PrimaryKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, created_at, timestamp, updated_at


class AddressModel(Base):
    __tablename__ = "address"

    id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(
        "userId", ForeignKey("user.id", name="FK_address_user", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    province: Mapped[str] = mapped_column(String(50), nullable=False)
    city: Mapped[str] = mapped_column(String(50), nullable=False)
    district: Mapped[str] = mapped_column(String(50), nullable=False)
    address: Mapped[str] = mapped_column(String(200), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        "isDefault", Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    deleted_at: Mapped[datetime | None] = timestamp("deletedAt", nullable=True)
    province_code: Mapped[str | None] = mapped_column(
        "provinceCode", String(12), comment="Province region code"
    )
    city_code: Mapped[str | None] = mapped_column(
        "cityCode", String(12), comment="City region code"
    )
    district_code: Mapped[str | None] = mapped_column(
        "districtCode", String(12), comment="District region code"
    )

    __table_args__ = (
        PrimaryKeyConstraint("id", name="PK_address_id"),
        Index("IDX_address_cityCode", "cityCode"),
        Index("IDX_address_deletedAt", "deletedAt", postgresql_where=text('"deletedAt" IS NULL')),
        Index("IDX_address_districtCode", "districtCode"),
        Index("IDX_address_provinceCode", "provinceCode"),
        Index("IDX_address_userId", "userId"),
        Index(
            "IDX_address_userId_isDefault_createdAt",
            "userId",
            text('"isDefault" DESC'),
            text('"createdAt" DESC'),
        ),
        Index(
            "UQ_address_userId_default",
            "userId",
            unique=True,
            postgresql_where=text('"isDefault" AND ("deletedAt" IS NULL)'),
        ),
    )
