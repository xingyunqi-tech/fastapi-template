lazy from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "IDX_%(table_name)s_%(column_0_N_name)s",
    "uq": "UQ_%(table_name)s_%(column_0_N_name)s",
    "ck": "CK_%(table_name)s_%(constraint_name)s",
    "fk": "FK_%(table_name)s_%(column_0_N_name)s",
    "pk": "PK_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def timestamp(name: str, *, nullable: bool = False) -> Mapped[datetime]:
    """`timestamp without time zone`，约定存 UTC。"""
    return mapped_column(name, DateTime(), nullable=nullable)


def created_at() -> Mapped[datetime]:
    return mapped_column("createdAt", DateTime(), nullable=False, server_default=func.now())


def updated_at() -> Mapped[datetime]:
    return mapped_column(
        "updatedAt", DateTime(), nullable=False, server_default=func.now(), onupdate=func.now()
    )
