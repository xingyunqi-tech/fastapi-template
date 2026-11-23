from enum import StrEnum

from pydantic import Field

from app.config.base import SettingsGroup


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class LogFormat(StrEnum):
    JSON = "json"
    TEXT = "text"


class LogSettings(SettingsGroup):
    """未设置时生产 `info` + `json`，其他环境 `debug` + `text`。"""

    level: LogLevel | None = Field(default=None, validation_alias="LOG_LEVEL")
    format: LogFormat | None = Field(default=None, validation_alias="LOG_FORMAT")

    def resolved_level(self, production: bool) -> LogLevel:
        if self.level is not None:
            return self.level
        return LogLevel.INFO if production else LogLevel.DEBUG

    def resolved_format(self, production: bool) -> LogFormat:
        if self.format is not None:
            return self.format
        return LogFormat.JSON if production else LogFormat.TEXT
