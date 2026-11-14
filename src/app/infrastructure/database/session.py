"""异步引擎与每请求一个 AsyncSession：成功提交、异常回滚，提交后执行登记的副作用。"""

import logging
import ssl
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.common.exceptions import HttpError
from app.config import DatabaseSettings

logger = logging.getLogger("app.database")

type AfterCommit = Callable[[], Awaitable[None]]
_AFTER_COMMIT_KEY = "after_commit"


class Database:
    def __init__(
        self,
        engine: AsyncEngine | None,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self.engine = engine
        self.sessionmaker = sessionmaker

    @classmethod
    def from_settings(
        cls,
        settings: DatabaseSettings,
        *,
        max_connections: int | None = None,
        processes: int = 1,
    ) -> Database:
        """`max_connections`（缺省取 `DATABASE_MAX_CONNECTIONS`）为合计上限，按进程数均分。"""
        total = settings.max_connections if max_connections is None else max_connections
        limit = max(total // processes, 1)
        engine = create_async_engine(
            settings.sqlalchemy_url(),
            pool_size=min(limit, 20),
            max_overflow=max(limit - 20, 0),
            pool_pre_ping=True,
            pool_timeout=settings.connection_timeout_ms / 1000,
            connect_args=_connect_args(settings),
        )
        return cls(engine, async_sessionmaker(engine, expire_on_commit=False))

    @classmethod
    def bound_to(cls, connection: AsyncConnection) -> Database:
        """绑定到已开启事务的连接：提交只释放 SAVEPOINT，由连接所有者决定最终回滚。"""
        return cls(
            None,
            async_sessionmaker(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ),
        )

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncSession]:
        """成功时提交并执行提交后副作用；业务错误（HttpError）保留已写入的数据后再抛出；其它异常回滚。"""
        async with self.sessionmaker() as session:
            try:
                yield session
            except HttpError:
                session.info.pop(_AFTER_COMMIT_KEY, None)
                await session.commit()
                raise
            except BaseException:
                await session.rollback()
                raise
            await session.commit()
            for callback in session.info.pop(_AFTER_COMMIT_KEY, []):
                try:
                    await callback()
                except Exception:
                    logger.exception("after-commit callback failed")

    async def dispose(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()


def _connect_args(settings: DatabaseSettings) -> dict[str, Any]:
    args: dict[str, Any] = {
        "server_settings": {"timezone": "UTC"},
        "timeout": settings.connection_timeout_ms / 1000,
    }
    context: ssl.SSLContext | None = settings.ssl_context()
    if context is not None:
        args["ssl"] = context
    return args


def after_commit(session: AsyncSession, callback: AfterCommit) -> None:
    """登记在事务提交成功后执行的副作用（发邮件、投递任务等）。"""
    session.info.setdefault(_AFTER_COMMIT_KEY, []).append(callback)


async def _db_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.context.database
    async with database.transaction() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(_db_session, scope="function")]


def affected_rows(result: Result[Any]) -> int:
    """UPDATE / DELETE 影响的行数。"""
    return int(getattr(result, "rowcount", 0))
