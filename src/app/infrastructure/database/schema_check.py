"""DB_SCHEMA_OWNER=external 时校验共享库包含 ORM 模型所需的全部表与列。"""

from sqlalchemy import text
lazy from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.infrastructure.database.models import Base


class SchemaMismatchError(RuntimeError):
    pass


async def missing_columns(connection: AsyncConnection) -> list[str]:
    rows = await connection.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        )
    )
    existing = {(row.table_name, row.column_name) for row in rows}
    return [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if (table.name, column.name) not in existing
    ]


async def ensure_external_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        missing = await missing_columns(connection)
    if missing:
        raise SchemaMismatchError(f"Database schema is missing: {', '.join(missing)}")
