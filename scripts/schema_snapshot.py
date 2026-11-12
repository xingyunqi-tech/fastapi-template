# /// script
# requires-python = ">=3.12"
# dependencies = ["asyncpg>=0.30"]
# ///
"""输出数据库表结构的规范化快照（列、约束、索引、注释），用于与 nestjs 迁移结果比对。

用法：schema_snapshot.py DSN [TABLE ...]   （DSN 为 postgresql://...，不传 TABLE 时为全部表）
"""

import asyncio
import sys

import asyncpg

COLUMNS = """
select c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
       pg_get_expr(d.adbin, d.adrelid), col_description(c.oid, a.attnum)
from pg_attribute a
join pg_class c on c.oid = a.attrelid
join pg_namespace n on n.oid = c.relnamespace
left join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum
where n.nspname = 'public' and c.relkind = 'r' and a.attnum > 0 and not a.attisdropped
"""
CONSTRAINTS = """
select c.relname, con.conname, pg_get_constraintdef(con.oid)
from pg_constraint con
join pg_class c on c.oid = con.conrelid
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and con.contype <> 'n'
"""
INDEXES = """
select tablename, indexname, indexdef from pg_indexes
where schemaname = 'public'
  and indexname not in (select conname from pg_constraint)
"""
TABLE_COMMENTS = """
select c.relname, obj_description(c.oid, 'pg_class')
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind = 'r'
"""


async def snapshot(dsn: str, tables: set[str]) -> list[str]:
    connection = await asyncpg.connect(dsn)
    try:
        lines: list[str] = []

        def wanted(table: str) -> bool:
            return not tables or table in tables

        for table, column, kind, not_null, default, comment in await connection.fetch(COLUMNS):
            if wanted(table):
                null = "NOT NULL" if not_null else "NULL"
                extra = f" DEFAULT {default}" if default else ""
                note = f" COMMENT {comment!r}" if comment else ""
                lines.append(f"column {table}.{column} {kind} {null}{extra}{note}")
        for table, name, definition in await connection.fetch(CONSTRAINTS):
            if wanted(table):
                lines.append(f"constraint {table}.{name} {definition}")
        for table, name, definition in await connection.fetch(INDEXES):
            if wanted(table):
                lines.append(f"index {table}.{name} {definition}")
        for table, comment in await connection.fetch(TABLE_COMMENTS):
            if wanted(table) and comment:
                lines.append(f"comment {table} {comment!r}")
        return sorted(lines)
    finally:
        await connection.close()


def main(argv: list[str]) -> int:
    lines = asyncio.run(snapshot(argv[1], set(argv[2:])))
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
