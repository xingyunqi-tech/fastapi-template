from pydantic import Field

from app.config.base import EnvFlag, SettingsGroup


class ObservabilitySettings(SettingsGroup):
    """Sentry 与 OpenTelemetry，默认关闭。"""

    sentry_dsn: str | None = Field(default=None, validation_alias="SENTRY_DSN")
    otel_enabled: EnvFlag = Field(default=False, validation_alias="OTEL_ENABLED")
    otel_endpoint: str | None = Field(default=None, validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT")
