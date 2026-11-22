from enum import StrEnum
from typing import Any, ClassVar, Final
from uuid import UUID, uuid7
lazy from datetime import datetime

from sqlalchemy import DateTime, MetaData, Uuid, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION: Final = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Branch(StrEnum):
    """Alembic 分支；表的 `info["branch"]` 取其一。"""

    PLATFORM = "platform"
    IDENTITY = "identity"
    MODULES = "modules"


class Base(DeclarativeBase):
    """子类须声明 `__branch__`，写入表的 `info["branch"]`。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    __branch__: ClassVar[Branch]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        table = cls.__dict__.get("__table__")
        if table is None:
            return
        branch = cls.__dict__.get("__branch__")
        if not isinstance(branch, Branch):
            raise TypeError(f"{cls.__name__} 须声明 __branch__")
        table.info["branch"] = str(branch)


def uuid_pk() -> Mapped[UUID]:
    """UUIDv7 主键，应用侧生成，库侧默认 `uuidv7()`。"""
    return mapped_column(Uuid, primary_key=True, default=uuid7, server_default=text("uuidv7()"))


def timestamp(*, nullable: bool = False) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
