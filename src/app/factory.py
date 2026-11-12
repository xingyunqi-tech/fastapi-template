from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

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
from app.core.users import router as users
from app.infrastructure.cache import Cache, MemoryCache, RedisCache
from app.infrastructure.database.schema_check import ensure_external_schema
from app.infrastructure.database.session import Database
from app.integrations.mail import LoggingMailer
from app.modules.home import router as home

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

PRODUCTION_CORS_METHODS = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
PRODUCTION_CORS_HEADERS = "Content-Type,Authorization,Accept-Language"


def build_context(settings: Settings) -> AppContext:
    cache: Cache
    if settings.redis.enabled:
        cache = RedisCache(Redis.from_url(settings.redis.url()))
    else:
        cache = MemoryCache()
    return AppContext(
        settings=settings,
        database=Database.from_settings(settings.database),
        cache=cache,
        passwords=Passwords(settings.auth.password_hasher),
        mailer=LoggingMailer(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    context: AppContext = app.state.context
    database_settings = context.settings.database
    if database_settings.schema_owner is SchemaOwner.EXTERNAL and context.database.engine:
        await ensure_external_schema(context.database.engine)
    yield
    await context.database.dispose()
    if isinstance(context.cache, RedisCache):
        await context.cache.client.aclose()


def create_app(
    settings: Settings | None = None,
    extra_routers: Sequence[tuple[ApiRouter, str]] = (),
) -> FastAPI:
    settings = settings or get_settings()
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
    register_exception_handlers(app, translator)
    if settings.app.swagger_enabled:
        mount_docs(app, settings.app.openapi_baseline_path)

    api_prefix = f"/{settings.app.api_prefix}"
    include_routes(app, users.router, api_prefix)
    include_routes(app, auth.router, api_prefix)
    include_routes(app, home.root_router)
    include_routes(app, home.api_router, api_prefix)
    for router, prefix in extra_routers:
        include_routes(app, router, prefix)
    finalize_routes(app)

    app.add_middleware(CatchAllMiddleware, translator=translator)
    app.add_middleware(RawPathMiddleware)
    app.add_middleware(JsonBodyMiddleware, translator=translator)
    app.add_middleware(SecurityHeadersMiddleware)
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
    return app
