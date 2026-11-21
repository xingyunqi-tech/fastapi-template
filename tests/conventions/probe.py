"""约定测试的探针路由与测试专用应用，不依赖数据库与业务接口。"""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Path, Query
from pydantic import Field

from app.common.errors import AppError
from app.common.pagination import (
    CursorPage,
    CursorParams,
    Page,
    PageParams,
    SortField,
    encode_cursor,
    sort_param,
)
from app.common.routing import API_PREFIX, ApiRouter, Auth
from app.common.types import ApiModel, Date, Email, Money, Phone, UtcDatetime, UUIDv7
from app.config import Settings
from app.factory import create_app

PROBE = f"{API_PREFIX}/v1/probe"
PROBE_ID = UUID("019a8f3c-6b1e-7d2a-9c4f-1e2d3c4b5a69")


class Color(StrEnum):
    LIGHT_BLUE = "light_blue"
    RED = "red"


class Line(ApiModel):
    qty: int = Field(ge=1)


class ProbeInput(ApiModel):
    email: Email
    phone: Phone | None = None
    name: str = Field(min_length=2, max_length=4)
    age: int = Field(default=18, ge=0, le=150)
    color: Color = Color.RED
    tags: list[str] = Field(default_factory=list, max_length=2)
    lines: list[Line] = Field(default_factory=list)
    ref: UUID | None = None
    at: UtcDatetime | None = None


class ProbeTypes(ApiModel):
    id: UUIDv7
    created_at: UtcDatetime
    birthday: Date
    amount: Money
    color: Color
    note: str | None


class ProbeItem(ApiModel):
    id: int
    name: str


class ProbeItemPage(Page[ProbeItem]):
    pass


class ProbeItemCursorPage(CursorPage[ProbeItem]):
    pass


class Counter:
    calls = 0


router = ApiRouter(prefix="/v1/probe", tags=["Probe"])
ProbeSort = Annotated[list[SortField], Depends(sort_param(("id", "name"), default="-id"))]


@router.endpoint("GET", "/types", operation_id="getProbeTypes", auth=Auth.PUBLIC)
async def get_probe_types() -> ProbeTypes:
    return ProbeTypes(
        id=PROBE_ID,
        created_at=datetime(2026, 11, 17, 16, 0, tzinfo=UTC).astimezone(),
        birthday=date(1990, 5, 6),
        amount=1999,
        color=Color.LIGHT_BLUE,
        note=None,
    )


@router.endpoint("POST", "/echo", operation_id="createProbeEcho", auth=Auth.PUBLIC, status=201)
async def create_probe_echo(body: ProbeInput) -> ProbeInput:
    return body


class UnionInput(ApiModel):
    value: int | str
    positive: int = Field(default=1, gt=0)


@router.endpoint("POST", "/union", operation_id="createProbeUnion", auth=Auth.PUBLIC)
async def create_probe_union(body: UnionInput) -> UnionInput:
    return body


@router.endpoint("DELETE", "/echo", operation_id="deleteProbeEcho", auth=Auth.PUBLIC)
async def delete_probe_echo() -> None:
    return None


@router.endpoint("GET", "/items/{id}", operation_id="getProbeItem", auth=Auth.PUBLIC)
async def get_probe_item(id: Annotated[UUID, Path()]) -> ProbeItem:
    raise AppError("common.notFound")


@router.endpoint("GET", "/page", operation_id="listProbePage", auth=Auth.PUBLIC)
async def list_probe_page(page: Annotated[PageParams, Query()], sort: ProbeSort) -> ProbeItemPage:
    items = [ProbeItem(id=i, name=f"n{i}") for i in range(1, 4)]
    for field in reversed(sort):
        items.sort(key=lambda item: getattr(item, field.field), reverse=field.descending)
    window = items[page.offset : page.offset + page.page_size]
    return ProbeItemPage.of(window, len(items), page)


@router.endpoint("GET", "/cursor", operation_id="listProbeCursor", auth=Auth.PUBLIC)
async def list_probe_cursor(params: Annotated[CursorParams, Query()]) -> ProbeItemCursorPage:
    start = 0
    decoded = params.decoded(str, int)
    if decoded is not None:
        start = int(decoded[1])
    items = [
        ProbeItem(id=i, name=f"n{i}") for i in range(start + 1, min(start + params.limit, 5) + 1)
    ]
    next_cursor = (
        encode_cursor(items[-1].name, items[-1].id) if items and items[-1].id < 5 else None
    )
    return ProbeItemCursorPage(items=items, next_cursor=next_cursor)


@router.endpoint("GET", "/me", operation_id="getProbeMe", auth=Auth.JWT)
async def get_probe_me() -> None:
    return None


@router.endpoint("GET", "/boom", operation_id="getProbeBoom", auth=Auth.PUBLIC)
async def get_probe_boom() -> None:
    raise RuntimeError("secret detail")


@router.endpoint(
    "POST", "/orders", operation_id="createProbeOrder", auth=Auth.PUBLIC, idempotent=True
)
async def create_probe_order(body: Line) -> dict[str, Any]:
    Counter.calls += 1
    if body.qty == 99:
        raise AppError("common.conflict")
    if body.qty == 500:
        raise RuntimeError("transient")
    return {"call": Counter.calls, "qty": body.qty}


def build_probe_app(settings: Settings | None = None) -> FastAPI:
    return create_app(settings or Settings.load(), extra_routers=[(router, API_PREFIX)])
