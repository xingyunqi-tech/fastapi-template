import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from redis.asyncio import Redis

from app.common.context import AppContext
from app.common.docs import mount_docs
from app.common.errors import ErrorTranslator
from app.common.handlers import register_exception_handlers
from app.common.i18n import get_i18n
from app.common.middleware import (
    CatchAllMiddleware,
    CorsMiddleware,
    JsonBodyMiddleware,
    RawPathMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.common.routing import ApiRouter, finalize_routes, include_routes
from app.common.security import Passwords
from app.config import SchemaOwner, Settings, get_settings
from app.core.auth import deps as _auth_deps  # noqa: F401
from app.core.auth import router as auth
from app.core.users import account_cleaner as _users_account_cleaner  # noqa: F401
from app.core.users import router as users
from app.infrastructure.account_cleanup import router as account_cleanup
from app.infrastructure.alerting import AdminAlertService, AlertLevel
from app.infrastructure.cache import Cache, MemoryCache, RedisCache
from app.infrastructure.database.schema_check import ensure_external_schema
from app.infrastructure.database.session import Database
from app.infrastructure.health import router as health
from app.infrastructure.logging import configure_logging
from app.infrastructure.logging.middleware import RequestLoggingMiddleware
from app.infrastructure.metrics import mark_process_dead
from app.infrastructure.metrics import router as metrics
from app.infrastructure.metrics.database import instrument_sqlalchemy
from app.infrastructure.metrics.middleware import MetricsMiddleware
from app.infrastructure.observability import setup_otel, setup_sentry
from app.infrastructure.observability import shutdown as shutdown_otel
from app.infrastructure.queue import TaskQueue
from app.infrastructure.queue.mail import QueueMailer
from app.infrastructure.throttler import RateLimitHeadersMiddleware, ThrottlerStorage
from app.integrations.mail import LoggingMailer, Mailer
from app.modules.addresses import account_cleaner as _addresses_account_cleaner  # noqa: F401
from app.modules.addresses import router as addresses
from app.modules.app_profile import router as app_profile
from app.modules.config import router as app_config
from app.modules.consents import router as consents
from app.modules.favorites import account_cleaner as _favorites_account_cleaner  # noqa: F401
from app.modules.favorites import router as favorites
from app.modules.feedbacks import account_cleaner as _feedbacks_account_cleaner  # noqa: F401
from app.modules.feedbacks import router as feedbacks
from app.modules.files import account_cleaner as _files_account_cleaner  # noqa: F401
from app.modules.files import router as files
from app.modules.files.storage import close_s3_clients
from app.modules.home import router as home
from app.modules.notifications import account_cleaner as _notifications_account_cleaner  # noqa: F401
from app.modules.notifications import router as notifications
from app.modules.regions import router as regions

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

logger = logging.getLogger("app")

PRODUCTION_CORS_METHODS = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
PRODUCTION_CORS_HEADERS = "Content-Type,Authorization,Accept-Language"


def build_context(settings: Settings, *, max_connections: int | None = None) -> AppContext:
    """`max_connections` 为本进程连接上限；缺省按 API 进程数均分 `DATABASE_MAX_CONNECTIONS`。"""
    redis = Redis.from_url(settings.redis.url()) if settings.redis.enabled else None
    cache: Cache = RedisCache(redis) if redis is not None else MemoryCache()
    queue = TaskQueue.from_settings(settings)
    context = AppContext(
        settings=settings,
        database=(
            Database.from_settings(settings.database, processes=settings.app.web_concurrency)
            if max_connections is None
            else Database.from_settings(settings.database, max_connections=max_connections)
        ),
        cache=cache,
        passwords=Passwords(
            settings.auth.password_hasher, concurrency=settings.auth.hash_concurrency
        ),
        mailer=_mailer(settings, queue),
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


class _ContextAlerts:
    """按调用时的 app.state.context 写告警。"""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def raise_(
        self,
        type_: str,
        level: AlertLevel,
        message: str,
        payload: dict[str, Any] | None = None,
        dedup_key: str | None = None,
    ) -> None:
        context: AppContext = self.app.state.context
        await AdminAlertService(context.database).raise_(type_, level, message, payload, dedup_key)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    context: AppContext = app.state.context
    database_settings = context.settings.database
    if database_settings.schema_owner is SchemaOwner.EXTERNAL and context.database.engine:
        await ensure_external_schema(context.database.engine)
    if context.queue is not None:
        await context.queue.startup()
    yield
    if context.queue is not None:
        await context.queue.shutdown()
    if isinstance(context.mailer, QueueMailer):
        await context.mailer.drain()
    await asyncio.gather(*app.state.throttler._pending, return_exceptions=True)
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
    i18n = get_i18n(settings.app.fallback_language)
    translator = ErrorTranslator(i18n)
    app = FastAPI(
        title=settings.app.name,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        separate_input_output_schemas=False,
        redirect_slashes=False,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.context = build_context(settings)
    app.state.throttler = ThrottlerStorage(_ContextAlerts(app))
    register_exception_handlers(app, translator)
    if settings.app.swagger_enabled:
        mount_docs(app, settings.app.openapi_baseline_path)

    api_prefix = f"/{settings.app.api_prefix}"
    include_routes(app, users.router, api_prefix)
    for files_router in files.routers_for(settings.file.driver):
        include_routes(app, files_router, api_prefix)
    include_routes(app, auth.router, api_prefix)
    include_routes(app, notifications.router, api_prefix)
    include_routes(app, home.root_router)
    include_routes(app, home.api_router, api_prefix)
    include_routes(app, health.router, api_prefix)
    include_routes(app, addresses.router, api_prefix)
    include_routes(app, regions.router, api_prefix)
    include_routes(app, favorites.router, api_prefix)
    include_routes(app, app_config.router, api_prefix)
    include_routes(app, feedbacks.router, api_prefix)
    include_routes(app, consents.router, api_prefix)
    include_routes(app, account_cleanup.router, api_prefix)
    include_routes(app, app_profile.router, api_prefix)
    include_routes(app, app_profile.admin_router, api_prefix)
    include_routes(app, metrics.build_router(settings.metrics.path))
    for router, prefix in extra_routers:
        include_routes(app, router, prefix)
    finalize_routes(app)

    app.add_middleware(CatchAllMiddleware, translator=translator)
    app.add_middleware(RawPathMiddleware)
    app.add_middleware(JsonBodyMiddleware, translator=translator)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitHeadersMiddleware)
    if settings.metrics.enabled:
        app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    if settings.app.is_production and settings.app.frontend_domain:
        app.add_middleware(
            CorsMiddleware,
            origins=[o.strip() for o in settings.app.frontend_domain.split(",")],
            methods=PRODUCTION_CORS_METHODS,
            allowed_headers=PRODUCTION_CORS_HEADERS,
        )
    else:
        app.add_middleware(CorsMiddleware, origins=None)
    app.add_middleware(RequestContextMiddleware, i18n=i18n)
    setup_otel(app, settings, app.state.context.database.engine)
    return app
