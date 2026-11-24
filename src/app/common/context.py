"""应用级单例（配置、数据库、Redis、缓存、令牌、密码、邮件、队列、锁、限流、短信计数）。"""

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request
lazy import httpx
lazy from redis.asyncio import Redis

from app.config import Settings
from app.identity.providers.sms.store import SmsStore, sms_store
from app.infrastructure.cache import Cache
from app.infrastructure.database.session import Database
from app.infrastructure.events import EventPublisher, build_publisher
from app.infrastructure.mail import Mailer
from app.infrastructure.redis import LockService
from app.infrastructure.throttler import RateLimiter
from app.modules.files.urls import FileUrls
from app.security.jwt import TokenSigner, TokenVerifier
from app.security.passwords import Passwords
from app.security.revocation import Revocations
lazy from app.infrastructure.queue import TaskQueue


@dataclass(slots=True)
class AppContext:
    settings: Settings
    database: Database | None
    cache: Cache
    passwords: Passwords
    mailer: Mailer
    signer: TokenSigner | None
    """验证方为 None。"""
    verifier: TokenVerifier
    redis: Redis | None = None
    queue: TaskQueue | None = None
    http_transport: httpx.AsyncBaseTransport | None = None
    """调用短信、微信、支付宝接口的 httpx transport，为 None 时走网络。"""
    locks: LockService = field(init=False)
    revocations: Revocations = field(init=False)
    sms_store: SmsStore = field(init=False)
    rate_limiter: RateLimiter = field(init=False)
    events: EventPublisher = field(init=False)
    file_urls: FileUrls | None = field(init=False)
    """未设置 `STORAGE_DRIVER` 时为 None。"""

    def __post_init__(self) -> None:
        self.locks = LockService(self.redis, self.settings.telemetry.service_name)
        self.revocations = Revocations(self.redis, self.settings.auth.access_ttl)
        self.sms_store = sms_store(self.redis)
        self.rate_limiter = RateLimiter(self.redis)
        self.events = build_publisher(self.settings, self.redis)
        self.file_urls = (
            FileUrls.from_settings(self.settings) if self.settings.storage.driver else None
        )

    @property
    def db(self) -> Database:
        if self.database is None:
            raise RuntimeError("DATABASE_URL is not set")
        return self.database

    @property
    def token_signer(self) -> TokenSigner:
        if self.signer is None:
            raise RuntimeError("AUTH_SIGNING_KEY is not set")
        return self.signer

    @property
    def urls(self) -> FileUrls:
        if self.file_urls is None:
            raise RuntimeError("STORAGE_DRIVER is not set")
        return self.file_urls


def _context(request: Request) -> AppContext:
    context: AppContext = request.app.state.context
    return context


Context = Annotated[AppContext, Depends(_context)]
