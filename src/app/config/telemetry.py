from enum import StrEnum
from typing import Self

from pydantic import Field, SecretStr, model_validator

from app.config.base import EnvFlag, SettingsGroup


class MetricsExporter(StrEnum):
    PROMETHEUS = "prometheus"
    OTLP = "otlp"
    NONE = "none"


class TracesExporter(StrEnum):
    OTLP = "otlp"
    NONE = "none"


class TelemetrySettings(SettingsGroup):
    """标准 `OTEL_*` 变量的子集；`OTEL_TRACES_EXPORTER` 未设置时，配置了 OTLP 端点才导出链路。"""

    service_name: str = Field(min_length=1, validation_alias="OTEL_SERVICE_NAME")
    sdk_disabled: EnvFlag = Field(default=False, validation_alias="OTEL_SDK_DISABLED")
    metrics_exporter: MetricsExporter = Field(
        default=MetricsExporter.PROMETHEUS, validation_alias="OTEL_METRICS_EXPORTER"
    )
    traces_exporter: TracesExporter | None = Field(
        default=None, validation_alias="OTEL_TRACES_EXPORTER"
    )
    otlp_endpoint: str | None = Field(default=None, validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otlp_traces_endpoint: str | None = Field(
        default=None, validation_alias="OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
    )
    prometheus_host: str = Field(
        default="0.0.0.0",  # noqa: S104
        validation_alias="OTEL_EXPORTER_PROMETHEUS_HOST",
    )
    prometheus_port: int = Field(
        default=9464, ge=0, le=65535, validation_alias="OTEL_EXPORTER_PROMETHEUS_PORT"
    )
    metrics_token: SecretStr | None = Field(
        default=None, min_length=16, validation_alias="METRICS_TOKEN"
    )
    sentry_dsn: str | None = Field(default=None, validation_alias="SENTRY_DSN")

    @model_validator(mode="after")
    def _default_traces(self) -> Self:
        if self.traces_exporter is None:
            configured = self.otlp_endpoint or self.otlp_traces_endpoint
            self.traces_exporter = TracesExporter.OTLP if configured else TracesExporter.NONE
        return self

    @property
    def metrics_enabled(self) -> bool:
        return not self.sdk_disabled and self.metrics_exporter is not MetricsExporter.NONE

    @property
    def prometheus_enabled(self) -> bool:
        return not self.sdk_disabled and self.metrics_exporter is MetricsExporter.PROMETHEUS

    @property
    def traces_enabled(self) -> bool:
        return not self.sdk_disabled and self.traces_exporter is TracesExporter.OTLP
