"""Sentry（`SENTRY_DSN`）与 OpenTelemetry（`OTEL_ENABLED`），默认均不启用。"""

from typing import Final

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
lazy from sqlalchemy.ext.asyncio import AsyncEngine

lazy from app.config import Settings

TRACES_PATH: Final = "/v1/traces"
_state: dict[str, TracerProvider | None] = {"provider": None}
_sentry: dict[str, str | None] = {"dsn": None}


def setup_sentry(settings: Settings) -> bool:
    dsn = settings.observability.sentry_dsn
    if not dsn:
        return False
    if _sentry["dsn"] != dsn:
        sentry_sdk.init(dsn=dsn, environment=settings.app.env.value, send_default_pii=False)
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
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
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
