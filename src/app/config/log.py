from enum import StrEnum
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from app.config.base import SettingsGroup


class LogLevel(StrEnum):
    FATAL = "fatal"
    ERROR = "error"
    WARN = "warn"
    INFO = "info"
    DEBUG = "debug"
    TRACE = "trace"
    SILENT = "silent"


def _is_true(value: Any) -> Any:
    return value == "true" if isinstance(value, str) else value


class LogSettings(SettingsGroup):
    """nestjs logger.config：未设置时生产 info + JSON，其它环境 debug + 彩色单行。"""

    level: LogLevel | None = Field(default=None, validation_alias="LOG_LEVEL")
    pretty_print: Annotated[bool | None, BeforeValidator(_is_true)] = Field(
        default=None, validation_alias="LOG_PRETTY_PRINT"
    )

    def resolved_level(self, production: bool) -> LogLevel:
        if self.level is not None:
            return self.level
        return LogLevel.INFO if production else LogLevel.DEBUG

    def resolved_pretty(self, production: bool) -> bool:
        return self.pretty_print if self.pretty_print is not None else not production
