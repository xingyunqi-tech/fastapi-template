import platform
import resource
import sys
import time
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
lazy from fastapi import Request

from app.common.routing import ApiRouter, Auth

_STARTED = time.monotonic()


class AppInfoDto(BaseModel):
    name: str


class MemoryDto(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    heap_used: int
    heap_total: int
    rss: int


class HealthCheckDto(BaseModel):
    status: str
    timestamp: str
    uptime: int
    memory: MemoryDto
    node: str


def _rss_megabytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(peak / divisor)


root_router = ApiRouter(tags=["Home"])
api_router = ApiRouter(tags=["Home"])


@root_router.endpoint(
    "GET", "/", operation_id="getAppInfo", auth=Auth.PUBLIC, summary="获取应用信息"
)
async def app_info(request: Request) -> AppInfoDto:
    return AppInfoDto(name=request.app.state.settings.app.name)


@api_router.endpoint(
    "GET",
    "/health",
    operation_id="simpleHealthCheck",
    auth=Auth.PUBLIC,
    summary="简单健康检查",
)
async def health_check() -> HealthCheckDto:
    rss = _rss_megabytes()
    now = datetime.now(UTC)
    return HealthCheckDto(
        status="ok",
        timestamp=now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
        uptime=int(time.monotonic() - _STARTED),
        memory=MemoryDto(heap_used=rss, heap_total=rss, rss=rss),
        node=f"{platform.python_implementation()} {platform.python_version()}",
    )
