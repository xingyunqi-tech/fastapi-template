lazy from collections.abc import Sequence

from fastapi import FastAPI

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
from app.config import Settings, get_settings
from app.modules.home import router as home

PRODUCTION_CORS_METHODS = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
PRODUCTION_CORS_HEADERS = "Content-Type,Authorization,Accept-Language"


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
    )
    app.state.settings = settings
    register_exception_handlers(app, translator)
    if settings.app.swagger_enabled:
        mount_docs(app, settings.app.openapi_baseline_path)

    api_prefix = f"/{settings.app.api_prefix}"
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
