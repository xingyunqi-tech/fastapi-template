"""与 nestjs MetricsModule 同名、同标签、同桶的 Prometheus 指标，以及供其它模块调用的记录函数。"""

import logging
import os
from pathlib import Path
from typing import Final
lazy from collections.abc import Iterable, Iterator

import prometheus_client
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, multiprocess
from prometheus_client.exposition import generate_latest
from prometheus_client.metrics_core import Metric
from prometheus_client.registry import Collector

prometheus_client.disable_created_metrics()  # type: ignore[no-untyped-call]

METRICS_CONTENT_TYPE: Final = "text/plain; version=0.0.4; charset=utf-8"
UNMATCHED_ROUTE: Final = "<unmatched>"
MULTIPROC_ENV: Final = "PROMETHEUS_MULTIPROC_DIR"

APP_REGISTRY: Final = CollectorRegistry(auto_describe=True)

logger = logging.getLogger("app.metrics")

http_requests_total: Final = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "path", "status"],
    registry=APP_REGISTRY,
)
http_request_duration_seconds: Final = Histogram(
    "http_request_duration_seconds",
    "Duration of HTTP requests in seconds",
    ["method", "path", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=APP_REGISTRY,
)
http_active_connections: Final = Gauge(
    "http_active_connections",
    "Number of active HTTP connections",
    registry=APP_REGISTRY,
    multiprocess_mode="livesum",
)
database_query_duration_seconds: Final = Histogram(
    "database_query_duration_seconds",
    "Duration of database queries in seconds",
    ["operation", "table"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
    registry=APP_REGISTRY,
)
cache_hits_total: Final = Counter(
    "cache_hits_total", "Total number of cache hits", ["cache_name"], registry=APP_REGISTRY
)
cache_misses_total: Final = Counter(
    "cache_misses_total", "Total number of cache misses", ["cache_name"], registry=APP_REGISTRY
)
queue_jobs_total: Final = Counter(
    "queue_jobs_total",
    "Total number of queue jobs",
    ["queue_name", "status"],
    registry=APP_REGISTRY,
)
admin_alerts_total: Final = Counter(
    "admin_alerts_total",
    "Total number of admin alerts raised",
    ["type", "level"],
    registry=APP_REGISTRY,
)
admin_alerts_deduplicated_total: Final = Counter(
    "admin_alerts_deduplicated_total",
    "Total number of admin alerts suppressed by an open alert with the same dedup key",
    ["type"],
    registry=APP_REGISTRY,
)
throttler_storage_fallback_total: Final = Counter(
    "throttler_storage_fallback_total",
    "Total number of rate limit checks served by the in-memory storage because Redis was "
    "unavailable",
    registry=APP_REGISTRY,
)


def record_cache_hit(cache_name: str) -> None:
    cache_hits_total.labels(cache_name=cache_name).inc()


def record_cache_miss(cache_name: str) -> None:
    cache_misses_total.labels(cache_name=cache_name).inc()


def record_queue_job(queue_name: str, status: str) -> None:
    queue_jobs_total.labels(queue_name=queue_name, status=status).inc()


def record_admin_alert(alert_type: str, level: str) -> None:
    admin_alerts_total.labels(type=alert_type, level=level).inc()


def record_admin_alert_deduplicated(alert_type: str) -> None:
    admin_alerts_deduplicated_total.labels(type=alert_type).inc()


def observe_database_query(operation: str, table: str, seconds: float) -> None:
    database_query_duration_seconds.labels(operation=operation, table=table).observe(seconds)


def multiprocess_enabled() -> bool:
    return bool(os.environ.get(MULTIPROC_ENV))


class _AppLabeled(Collector):
    """给每个样本追加 `app` 标签（nestjs `defaultLabels`）。"""

    def __init__(self, sources: Iterable[Collector], app_name: str) -> None:
        self.sources = list(sources)
        self.app_name = app_name

    def collect(self) -> Iterator[Metric]:
        for source in self.sources:
            for metric in source.collect():
                labeled = Metric(metric.name, metric.documentation, metric.type, metric.unit)
                labeled.samples = [
                    sample._replace(labels={**sample.labels, "app": self.app_name})
                    for sample in metric.samples
                ]
                yield labeled


def render(app_name: str, *, include_default: bool) -> bytes:
    """`include_default` 对应 nestjs `defaultMetrics.enabled`（进程级默认指标）。"""
    sources: list[Collector]
    if multiprocess_enabled():
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        sources = [registry]
    else:
        sources = [APP_REGISTRY]
        if include_default:
            sources.insert(0, prometheus_client.REGISTRY)
    return generate_latest(_AppLabeled(sources, app_name))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _file_pid(path: Path) -> int | None:
    suffix = path.stem.rpartition("_")[2]
    return int(suffix) if suffix.isdigit() else None


def prepare_multiprocess(web_concurrency: int) -> None:
    """派生 worker 之前调用：删除多进程目录中已退出进程的 `*.db`；多 worker 未设置该目录时告警。"""
    directory = os.environ.get(MULTIPROC_ENV)
    if directory:
        for path in Path(directory).glob("*.db"):
            pid = _file_pid(path)
            if pid is not None and not _pid_alive(pid):
                path.unlink(missing_ok=True)
    elif web_concurrency > 1:
        logger.warning(
            "WEB_CONCURRENCY=%d but %s is not set: each worker keeps its own metric values "
            "and /metrics only reports the worker that serves the scrape",
            web_concurrency,
            MULTIPROC_ENV,
        )


def mark_process_dead() -> None:
    if multiprocess_enabled():
        multiprocess.mark_process_dead(os.getpid())  # type: ignore[no-untyped-call]
