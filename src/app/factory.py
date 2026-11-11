from fastapi import FastAPI

from app.common.docs import mount_docs
from app.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.app.name,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        separate_input_output_schemas=False,
        redirect_slashes=False,
    )
    app.state.settings = settings
    if settings.app.swagger_enabled:
        mount_docs(app, settings.app.openapi_baseline_path)
    return app
