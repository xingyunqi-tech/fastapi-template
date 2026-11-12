"""应用级单例（配置、数据库、Redis、缓存、密码哈希、邮件、队列、锁），挂在 app.state 上。"""

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request
lazy import httpx
lazy from redis.asyncio import Redis

from app.common.security import Passwords
from app.config import Settings
from app.core.users.serializers import FileUrls
from app.infrastructure.cache import Cache
from app.infrastructure.database.session import Database
from app.infrastructure.redis import LockService
from app.integrations.mail import Mailer
lazy from app.infrastructure.queue import TaskQueue


@dataclass(slots=True)
class AppContext:
    settings: Settings
    database: Database
    cache: Cache
    passwords: Passwords
    mailer: Mailer
    redis: Redis | None = None
    queue: TaskQueue | None = None
    http_transport: httpx.AsyncBaseTransport | None = None
    """调用短信、微信、支付宝等外部接口的 httpx transport，为 None 时走网络。"""
    locks: LockService = field(init=False)

    def __post_init__(self) -> None:
        self.locks = LockService(self.redis)

    @property
    def file_urls(self) -> FileUrls:
        return FileUrls.from_settings(self.settings)


def _context(request: Request) -> AppContext:
    context: AppContext = request.app.state.context
    return context


Context = Annotated[AppContext, Depends(_context)]
