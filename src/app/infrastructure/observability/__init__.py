"""Sentry（`SENTRY_DSN`）与 OpenTelemetry（`OTEL_ENABLED`），默认均不启用；出站 URL 不含查询串。"""

from typing import Any, Final
from urllib.parse import urlsplit

lazy import sentry_sdk
lazy from fastapi import FastAPI
lazy from opentelemetry import trace
lazy from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
lazy from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
lazy from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
lazy from opentelemetry.instrumentation.redis import RedisInstrumentor
lazy from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
lazy from opentelemetry.sdk.resources import Resource
lazy from opentelemetry.sdk.trace import TracerProvider
lazy from opentelemetry.sdk.trace.export import BatchSpanProcessor
lazy from opentelemetry.trace import Span
lazy from sqlalchemy.ext.asyncio import AsyncEngine

lazy from app.config import Settings

TRACES_PATH: Final = "/v1/traces"
URL_ATTRIBUTES: Final = ("url.full", "http.url")
_state: dict[str, TracerProvider | None] = {"provider": None}
_sentry: dict[str, str | None] = {"dsn": None}


def without_query(url: str) -> str:
    return urlsplit(url)._replace(query="", fragment="").geturl()


def strip_span_query(span: Span, request: object) -> None:
    """出站请求 span 的 URL 去掉查询串。"""
    url = getattr(request, "url", None)
    if url is not None and span.is_recording():
        for name in URL_ATTRIBUTES:
            span.set_attribute(name, without_query(str(url)))


async def strip_span_query_async(span: Span, request: object) -> None:
    strip_span_query(span, request)


def strip_breadcrumb_query(crumb: dict[str, Any], hint: object) -> dict[str, Any]:
    """HTTP 面包屑去掉 URL 查询串。"""
    data = crumb.get("data")
    if isinstance(data, dict):
        data.pop("http.query", None)
        data.pop("http.fragment", None)
        if isinstance(data.get("url"), str):
            data["url"] = without_query(data["url"])
    return crumb


def setup_sentry(settings: Settings) -> bool:
    dsn = settings.observability.sentry_dsn
    if not dsn:
        return False
    if _sentry["dsn"] != dsn:
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.app.env.value,
            send_default_pii=False,
            include_local_variables=False,
            before_breadcrumb=strip_breadcrumb_query,
        )
        _sentry["dsn"] = dsn
    return True


def _provider(settings: Settings) -> TracerProvider:
    provider = _state["provider"]
    if provider is not None:
        return provider
    endpoint = settings.observability.otel_endpoint
    exporter = (
        OTLPSpanExporter(endpoint=endpoint.rstrip("/") + TRACES_PATH)
        if endpoint
        else OTLPSpanExporter()
    )
    provider = TracerProvider(resource=Resource.create({"service.name": settings.app.name}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    RedisInstrumentor().instrument(tracer_provider=provider)
    HTTPXClientInstrumentor().instrument(
        tracer_provider=provider,
        request_hook=strip_span_query,
        async_request_hook=strip_span_query_async,
    )
    _state["provider"] = provider
    return provider


def setup_otel(app: FastAPI, settings: Settings, engine: AsyncEngine | None) -> bool:
    """自动埋点 FastAPI、SQLAlchemy、Redis、httpx，OTLP/HTTP 导出。"""
    if not settings.observability.otel_enabled:
        return False
    provider = _provider(settings)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    if engine is not None:
        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=provider)
    return True


def shutdown() -> None:
    provider = _state["provider"]
    if provider is not None:
        provider.force_flush()
