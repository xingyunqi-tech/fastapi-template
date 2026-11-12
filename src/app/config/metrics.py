from typing import Annotated, Any

from pydantic import BeforeValidator, Field, SecretStr

from app.config.base import SettingsGroup


def _not_false(value: Any) -> Any:
    return value != "false" if isinstance(value, str) else value


class MetricsSettings(SettingsGroup):
    """nestjs metrics.config：`METRICS_ENABLED` 只有字符串 `false` 视为关闭。"""

    enabled: Annotated[bool, BeforeValidator(_not_false)] = Field(
        default=True, validation_alias="METRICS_ENABLED"
    )
    path: str = Field(default="metrics", validation_alias="METRICS_PATH")
    token: SecretStr | None = Field(default=None, min_length=16, validation_alias="METRICS_TOKEN")
    app_label: str = Field(default="nestjs-app", validation_alias="APP_NAME")
