import platform
import time
from datetime import UTC, datetime

import psutil  # type: ignore[import-untyped]
from fastapi import Request
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.common.routing import ApiRouter, Auth

_STARTED = time.monotonic()
MB = 1024 * 1024


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


def _memory() -> MemoryDto:
    """进程当前内存（MB）：heapUsed / heapTotal 取进程独占内存（USS），rss 取常驻内存。"""
    info = psutil.Process().memory_full_info()
    private = round(info.uss / MB)
    return MemoryDto(heap_used=private, heap_total=private, rss=round(info.rss / MB))


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
    now = datetime.now(UTC)
    return HealthCheckDto(
        status="ok",
        timestamp=now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
        uptime=int(time.monotonic() - _STARTED),
        memory=_memory(),
        node=f"{platform.python_implementation()} {platform.python_version()}",
    )
