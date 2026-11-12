from fastapi import Request
from starlette.responses import Response

from app.common.routing import ApiRouter, Auth
from app.infrastructure.metrics import METRICS_CONTENT_TYPE, render
from app.infrastructure.metrics.guard import monitoring_token as _monitoring_token  # noqa: F401


def build_router(path: str) -> ApiRouter:
    """`/{METRICS_PATH}`，不带 API 前缀、不进文档、不包信封。"""
    router = ApiRouter()

    @router.endpoint(
        "GET",
        f"/{path.strip('/')}",
        operation_id="getMetrics",
        auth=Auth.MONITORING,
        wrap=False,
        include_in_schema=False,
    )
    async def metrics(request: Request) -> Response:
        settings = request.app.state.context.settings
        body = render(settings.metrics.app_label, include_default=settings.metrics.enabled)
        return Response(body, media_type=METRICS_CONTENT_TYPE)

    return router
