"""SQLAlchemy 游标事件 → `database_query_duration_seconds{operation, table}`。"""

import re
import time
from typing import Any, Final

from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.infrastructure.metrics import observe_database_query

_STARTED_KEY: Final = "_metrics_query_started"
_TABLE_RE: Final = re.compile(
    r"\b(?:FROM|INTO|UPDATE|JOIN)\s+((?:\"[^\"]+\"|[\w$]+)(?:\.(?:\"[^\"]+\"|[\w$]+))?)",
    re.IGNORECASE,
)
_state = {"installed": False}


def classify(statement: str) -> tuple[str, str]:
    words = statement.lstrip(" (\n\t").split(None, 1)
    operation = words[0].upper() if words else "UNKNOWN"
    match = _TABLE_RE.search(statement)
    table = match.group(1).rsplit(".", 1)[-1].strip('"') if match else "unknown"
    return operation, table


def _before(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
    conn.info.setdefault(_STARTED_KEY, []).append(time.perf_counter())


def _after(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
    stack = conn.info.get(_STARTED_KEY)
    if not stack:
        return
    operation, table = classify(statement)
    observe_database_query(operation, table, time.perf_counter() - stack.pop())


def _failed(context: Any) -> None:
    stack = context.connection.info.get(_STARTED_KEY) if context.connection is not None else None
    if stack:
        stack.pop()


def instrument_sqlalchemy() -> None:
    if _state["installed"]:
        return
    event.listen(Engine, "before_cursor_execute", _before)
    event.listen(Engine, "after_cursor_execute", _after)
    event.listen(Engine, "handle_error", _failed)
    _state["installed"] = True
