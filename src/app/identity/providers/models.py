from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, Branch, created_at, uuid_pk


class UserIdentityModel(Base):
    """第三方身份；支付宝的 `openid` 列存 `open_id`（旧应用为 `user_id`）。"""

    __tablename__ = "user_identities"
    __branch__ = Branch.IDENTITY

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    openid: Mapped[str] = mapped_column(String(128), nullable=False)
    union_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        UniqueConstraint("provider", "openid"),
        Index(
            "uq_user_identities_provider_union_id",
            "provider",
            "union_id",
            unique=True,
            postgresql_where=text("union_id IS NOT NULL"),
        ),
    )
