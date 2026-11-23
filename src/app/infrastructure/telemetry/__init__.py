"""OpenTelemetry 指标与链路、Sentry；HTTP 指标按稳定语义约定输出，出站 URL 不含查询串。"""

import os
from typing import Any, Final
from urllib.parse import urlsplit

from opentelemetry import metrics
from prometheus_client import CollectorRegistry
lazy import sentry_sdk
lazy from fastapi import FastAPI
lazy from opentelemetry import trace
lazy from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
lazy from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
lazy from opentelemetry.exporter.prometheus import PrometheusMetricReader
lazy from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
lazy from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
lazy from opentelemetry.instrumentation.redis import RedisInstrumentor
lazy from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
lazy from opentelemetry.sdk.metrics import MeterProvider
lazy from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
lazy from opentelemetry.sdk.resources import Resource
lazy from opentelemetry.sdk.trace import TracerProvider
lazy from opentelemetry.sdk.trace.export import BatchSpanProcessor
lazy from opentelemetry.trace import Span
lazy from prometheus_client import start_http_server
lazy from sqlalchemy.ext.asyncio import AsyncEngine

from app.common.middleware import current_request_id
lazy from app.config import Settings

os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

URL_ATTRIBUTES: Final = ("url.full", "http.url")
REGISTRY: Final = CollectorRegistry()
"""`PrometheusMetricReader` 写入的注册表，`/metrics` 与 worker 端口输出它。"""

_meter = metrics.get_meter("app")
queue_job_duration: Final = _meter.create_histogram(
    "queue.job.duration", unit="s", description="Duration of task queue jobs"
)
ratelimit_fallback: Final = _meter.create_counter(
    "ratelimit.fallback",
    description="Rate limit checks of private rules allowed because Redis was unavailable",
)

_state: dict[str, Any] = {"meters": None, "tracer": None, "sentry": None, "server": False}


def record_queue_job(queue: str, status: str, seconds: float) -> None:
    queue_job_duration.record(seconds, {"queue": queue, "status": status})


def record_ratelimit_fallback(rule: str) -> None:
    ratelimit_fallback.add(1, {"rule": rule})


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


def tag_request_id(event: Any, hint: Any) -> Any:
    request_id = current_request_id()
    if request_id is not None:
        event.setdefault("tags", {})["request_id"] = request_id
    return event


def setup_sentry(settings: Settings) -> bool:
    dsn = settings.telemetry.sentry_dsn
    if not dsn:
        return False
    if _state["sentry"] != dsn:
        sentry_sdk.init(
            dsn=dsn,
            environment=settings.app.env.value,
            send_default_pii=False,
            include_local_variables=False,
            before_breadcrumb=strip_breadcrumb_query,
            before_send=tag_request_id,
        )
        _state["sentry"] = dsn
    return True


def _resource(settings: Settings) -> Resource:
    return Resource.create({"service.name": settings.telemetry.service_name})


def build_metric_reader(settings: Settings) -> MetricReader:
    if settings.telemetry.prometheus_enabled:
        return PrometheusMetricReader(registry=REGISTRY)
    return PeriodicExportingMetricReader(OTLPMetricExporter())


def meter_provider(settings: Settings) -> MeterProvider | None:
    """进程内只创建一次，并设为全局 provider（自定义指标经全局 API 记录）。"""
    if not settings.telemetry.metrics_enabled:
        return None
    provider: MeterProvider | None = _state["meters"]
    if provider is None:
        provider = MeterProvider(
            metric_readers=[build_metric_reader(settings)], resource=_resource(settings)
        )
        metrics.set_meter_provider(provider)
        _state["meters"] = provider
    return provider


def tracer_provider(settings: Settings) -> TracerProvider | None:
    if not settings.telemetry.traces_enabled:
        return None
    provider: TracerProvider | None = _state["tracer"]
    if provider is None:
        provider = TracerProvider(resource=_resource(settings))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        RedisInstrumentor().instrument(tracer_provider=provider)
        HTTPXClientInstrumentor().instrument(
            tracer_provider=provider,
            request_hook=strip_span_query,
            async_request_hook=strip_span_query_async,
        )
        _state["tracer"] = provider
    return provider


def instrument_app(app: FastAPI, settings: Settings, engine: AsyncEngine | None) -> bool:
    """FastAPI 自动埋点（HTTP 指标与链路）；开启链路时另埋点 SQLAlchemy、Redis、httpx。"""
    meters = meter_provider(settings)
    tracer = tracer_provider(settings)
    if meters is None and tracer is None:
        return False
    FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer, meter_provider=meters)
    if tracer is not None and engine is not None:
        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=tracer)
    return True


def start_metrics_server(settings: Settings) -> bool:
    """worker / scheduler 进程在 `OTEL_EXPORTER_PROMETHEUS_HOST:PORT` 输出指标。"""
    if meter_provider(settings) is None or not settings.telemetry.prometheus_enabled:
        return False
    if not _state["server"]:
        start_http_server(
            settings.telemetry.prometheus_port,
            settings.telemetry.prometheus_host,
            registry=REGISTRY,
        )
        _state["server"] = True
    return True


def shutdown() -> None:
    tracer: TracerProvider | None = _state["tracer"]
    if tracer is not None:
        tracer.force_flush()
    meters: MeterProvider | None = _state["meters"]
    if meters is not None:
        meters.force_flush()
