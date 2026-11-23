"""异步引擎与每请求一个 AsyncSession：成功提交、异常回滚，提交或回滚后执行登记的副作用。"""

import logging
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

from app.common.errors import AppError
from app.config import DatabaseSettings

logger = logging.getLogger("app.database")

type AfterCommit = Callable[[], Awaitable[None]]
_AFTER_COMMIT_KEY = "after_commit"
_AFTER_ROLLBACK_KEY = "after_rollback"


async def _run(callbacks: list[AfterCommit], label: str) -> None:
    for callback in callbacks:
        try:
            await callback()
        except Exception:
            logger.exception("%s callback failed", label)


class Database:
    def __init__(
        self,
        engine: AsyncEngine | None,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self.engine = engine
        self.sessionmaker = sessionmaker

    @classmethod
    def from_settings(cls, settings: DatabaseSettings, *, pool_size: int | None = None) -> Database:
        """每进程一个连接池，大小缺省取 `DATABASE_POOL_SIZE`，不额外溢出。"""
        engine = create_async_engine(
            settings.sqlalchemy_url(),
            pool_size=pool_size or settings.pool_size,
            max_overflow=0,
            pool_pre_ping=True,
            pool_timeout=settings.pool_timeout.total_seconds(),
            connect_args=settings.connect_args(),
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
        """成功时提交并执行提交后副作用；业务错误（AppError）保留已写入的数据后再抛出；其它异常回滚。"""
        async with self.sessionmaker() as session:
            try:
                yield session
            except AppError:
                session.info.pop(_AFTER_COMMIT_KEY, None)
                session.info.pop(_AFTER_ROLLBACK_KEY, None)
                await session.commit()
                raise
            except BaseException:
                await session.rollback()
                await _run(session.info.pop(_AFTER_ROLLBACK_KEY, []), "after-rollback")
                raise
            try:
                await session.commit()
            except BaseException:
                await _run(session.info.pop(_AFTER_ROLLBACK_KEY, []), "after-rollback")
                raise
            session.info.pop(_AFTER_ROLLBACK_KEY, None)
            await _run(session.info.pop(_AFTER_COMMIT_KEY, []), "after-commit")

    async def dispose(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()


def after_commit(session: AsyncSession, callback: AfterCommit) -> None:
    """登记在事务提交成功后执行的副作用（发邮件、投递任务等）。"""
    session.info.setdefault(_AFTER_COMMIT_KEY, []).append(callback)


def after_rollback(session: AsyncSession, callback: AfterCommit) -> None:
    """登记在事务回滚或提交失败后执行的补偿（删除已写入的外部对象等）。"""
    session.info.setdefault(_AFTER_ROLLBACK_KEY, []).append(callback)


async def _db_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.context.database
    async with database.transaction() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(_db_session, scope="function")]


def affected_rows(result: Result[Any]) -> int:
    """UPDATE / DELETE 影响的行数。"""
    return int(getattr(result, "rowcount", 0))
