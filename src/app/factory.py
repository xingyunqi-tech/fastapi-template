import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from redis.asyncio import Redis
from starlette.middleware.cors import CORSMiddleware

from app.common.context import AppContext
from app.common.docs import mount_docs
from app.common.handlers import CatchAllMiddleware, register_exception_handlers
from app.common.i18n import get_i18n
from app.common.middleware import (
    CORS_ALLOWED_HEADERS,
    CORS_EXPOSED_HEADERS,
    CORS_METHODS,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.common.openapi import install_openapi, package_version
from app.common.routing import API_PREFIX, ApiRouter, finalize_routes, include_routes
from app.config import Settings, get_settings
from app.identity.auth import router as auth
from app.identity.keys import router as keys
from app.identity.me import router as me
from app.identity.sessions import account_cleaner as _sessions_account_cleaner  # noqa: F401
from app.identity.sessions import deps as _issuer_auth  # noqa: F401
from app.identity.users import router as users
from app.infrastructure.cache import Cache, MemoryCache, RedisCache
from app.infrastructure.database.session import Database
from app.infrastructure.health import router as health
from app.infrastructure.logging import configure_logging
from app.infrastructure.logging.middleware import RequestLoggingMiddleware
from app.infrastructure.mail import LoggingMailer, Mailer
from app.infrastructure.metrics import mark_process_dead
from app.infrastructure.metrics import router as metrics
from app.infrastructure.metrics.database import instrument_sqlalchemy
from app.infrastructure.metrics.middleware import MetricsMiddleware
from app.infrastructure.observability import setup_otel, setup_sentry
from app.infrastructure.observability import shutdown as shutdown_otel
from app.infrastructure.queue import TaskQueue
from app.infrastructure.queue.mail import QueueMailer
from app.infrastructure.throttler import RateLimitHeadersMiddleware, ThrottlerStorage
from app.modules.files import account_cleaner as _files_account_cleaner  # noqa: F401
from app.modules.files import router as files
from app.modules.files.storage import close_s3_clients
from app.security import deps as _security_auth  # noqa: F401
from app.security.jwt import TokenSigner, TokenVerifier
from app.security.keys import SigningKey, load_public_key
from app.security.passwords import Passwords

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

logger = logging.getLogger("app")


def build_tokens(settings: Settings) -> tuple[TokenSigner, TokenVerifier]:
    """签名密钥的公钥与 `AUTH_VERIFY_KEYS` 共同组成校验密钥集。"""
    auth = settings.auth
    key = SigningKey.from_pem(auth.signing_key.get_secret_value())
    signer = TokenSigner(key, issuer=auth.issuer, audience=auth.audience, ttl=auth.access_ttl)
    verifier = TokenVerifier(
        [key.private_key.public_key(), *(load_public_key(pem) for pem in auth.verify_keys)],
        issuer=auth.issuer,
        audience=auth.audience,
    )
    return signer, verifier


def build_context(settings: Settings, *, pool_size: int | None = None) -> AppContext:
    """`pool_size` 覆盖本进程的 `DATABASE_POOL_SIZE`。"""
    redis = Redis.from_url(settings.redis.url()) if settings.redis.enabled else None
    cache: Cache = RedisCache(redis) if redis is not None else MemoryCache()
    queue = TaskQueue.from_settings(settings)
    signer, verifier = build_tokens(settings)
    context = AppContext(
        settings=settings,
        database=Database.from_settings(settings.database, pool_size=pool_size),
        cache=cache,
        passwords=Passwords(concurrency=settings.auth.hash_concurrency),
        mailer=_mailer(settings, queue),
        signer=signer,
        verifier=verifier,
        redis=redis,
        queue=queue,
    )
    queue.bind(context)
    return context


def _mailer(settings: Settings, queue: TaskQueue) -> Mailer:
    if settings.mail.host:
        return QueueMailer(queue, settings)
    logger.warning("MAIL_HOST is not set, mails are only logged")
    return LoggingMailer()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    context: AppContext = app.state.context
    if context.queue is not None:
        await context.queue.startup()
    yield
    if context.queue is not None:
        await context.queue.shutdown()
    if isinstance(context.mailer, QueueMailer):
        await context.mailer.drain()
    await close_s3_clients()
    await context.database.dispose()
    if context.redis is not None:
        await context.redis.aclose()
    shutdown_otel()
    mark_process_dead()


def create_app(
    settings: Settings | None = None,
    extra_routers: Sequence[tuple[ApiRouter, str]] = (),
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    setup_sentry(settings)
    instrument_sqlalchemy()
    i18n = get_i18n(settings.app.default_locale)
    app = FastAPI(
        title=settings.app.name,
        version=package_version(),
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        separate_input_output_schemas=False,
        redirect_slashes=False,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.i18n = i18n
    app.state.context = build_context(settings)
    app.state.throttler = ThrottlerStorage()
    register_exception_handlers(app)
    install_openapi(app)
    if settings.app.docs_enabled:
        mount_docs(app)

    include_routes(app, auth.router, API_PREFIX)
    include_routes(app, me.router, API_PREFIX)
    include_routes(app, users.router, API_PREFIX)
    include_routes(app, keys.router)
    for files_router in files.routers_for(settings.file.driver):
        include_routes(app, files_router, API_PREFIX)
    include_routes(app, health.router, API_PREFIX)
    include_routes(app, metrics.build_router(settings.metrics.path))
    for router, prefix in extra_routers:
        include_routes(app, router, prefix)
    finalize_routes(app)

    app.add_middleware(CatchAllMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitHeadersMiddleware)
    if settings.metrics.enabled:
        app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app.cors_origins if settings.app.is_production else ["*"],
        allow_methods=list(CORS_METHODS),
        allow_headers=list(CORS_ALLOWED_HEADERS),
        expose_headers=list(CORS_EXPOSED_HEADERS),
        allow_credentials=False,
    )
    app.add_middleware(RequestContextMiddleware, i18n=i18n)
    setup_otel(app, settings, app.state.context.database.engine)
    return app
