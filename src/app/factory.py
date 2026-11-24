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
from app.common.routing import API_PREFIX, ApiRouter, Auth, finalize_routes, include_routes
from app.config import Settings, get_settings
from app.identity.auth import router as auth
from app.identity.keys import router as keys
from app.identity.me import router as me
from app.identity.sessions import account_cleaner as _sessions_account_cleaner  # noqa: F401
from app.identity.sessions.deps import touched_principal
from app.identity.users import router as users
from app.infrastructure.audit import router as audit
from app.infrastructure.cache import Cache, MemoryCache, RedisCache
from app.infrastructure.database.session import Database
from app.infrastructure.health import router as health
from app.infrastructure.logging import configure_logging
from app.infrastructure.logging.middleware import RequestLoggingMiddleware
from app.infrastructure.mail import LoggingMailer, Mailer
from app.infrastructure.queue import TaskQueue
from app.infrastructure.queue.mail import QueueMailer
from app.infrastructure.telemetry import instrument_app, setup_sentry
from app.infrastructure.telemetry import router as metrics
from app.infrastructure.telemetry import shutdown as shutdown_telemetry
from app.infrastructure.throttler import RateLimitHeadersMiddleware
from app.modules.files import account_cleaner as _files_account_cleaner  # noqa: F401
from app.modules.files import router as files
from app.modules.files.storage import close_s3_clients
from app.modules.notes import account_cleaner as _notes_account_cleaner  # noqa: F401
from app.modules.notes import router as notes
from app.security import deps as _security_auth  # noqa: F401
from app.security.jwks import JwksKeySet
from app.security.jwt import TokenSigner, TokenVerifier
from app.security.keys import SigningKey, load_public_key
from app.security.passwords import Passwords

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

logger = logging.getLogger("app")


def build_tokens(settings: Settings) -> tuple[TokenSigner | None, TokenVerifier]:
    """签发方用签名公钥与 `AUTH_VERIFY_KEYS` 校验；验证方从 `AUTH_JWKS_URL` 取公钥。"""
    auth = settings.auth
    if auth.jwks_url is not None:
        jwks = JwksKeySet(str(auth.jwks_url))
        return None, TokenVerifier(issuer=auth.issuer, audience=auth.audience, jwks=jwks)
    key = SigningKey.from_pem(auth.signing_pem())
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
    service = settings.telemetry.service_name
    cache: Cache = RedisCache(redis, service) if redis is not None else MemoryCache()
    queue = TaskQueue.from_settings(settings)
    signer, verifier = build_tokens(settings)
    context = AppContext(
        settings=settings,
        database=(
            Database.from_settings(settings.database, pool_size=pool_size)
            if settings.database.enabled
            else None
        ),
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
    if context.database is not None:
        await context.database.dispose()
    if context.redis is not None:
        await context.redis.aclose()
    shutdown_telemetry()


def create_app(
    settings: Settings | None = None,
    extra_routers: Sequence[tuple[ApiRouter, str]] = (),
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    setup_sentry(settings)
    i18n = get_i18n(settings.app.default_locale)
    app = FastAPI(
        title=settings.telemetry.service_name,
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
    register_exception_handlers(app)
    install_openapi(app)
    if settings.app.docs_enabled:
        mount_docs(app)

    mount_routes(app, settings)
    if settings.telemetry.prometheus_enabled:
        include_routes(app, metrics.router)
    for router, prefix in extra_routers:
        include_routes(app, router, prefix)
    finalize_routes(app)

    app.add_middleware(CatchAllMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitHeadersMiddleware)
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
    database = app.state.context.database
    instrument_app(app, settings, database.engine if database is not None else None)
    return app


def mount_routes(app: FastAPI, settings: Settings) -> None:
    """签发方挂载全部模块；验证方按已配置的数据库与存储挂载（方案 §7.5）。"""
    if settings.auth.is_issuer:
        app.state.auth_dependencies = {Auth.JWT: touched_principal}
        include_routes(app, auth.router, API_PREFIX)
        include_routes(app, me.router, API_PREFIX)
        include_routes(app, users.router, API_PREFIX)
        include_routes(app, keys.router)
    if settings.database.enabled:
        include_routes(app, audit.router, API_PREFIX)
        include_routes(app, notes.router, API_PREFIX)
    if settings.database.enabled and settings.storage.driver is not None:
        include_routes(app, files.router, API_PREFIX)
    include_routes(app, health.router)
