"""structlog：约定 §8 的 JSON 行（`LOG_FORMAT=json`）或彩色单行（`text`），含标准库日志。"""

import logging
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Final

import structlog
from opentelemetry import trace
lazy from structlog.typing import EventDict, Processor, WrappedLogger

from app.common.masking import mask_email, mask_phone
from app.common.middleware import current_request_id
from app.config import LogFormat, LogLevel
lazy from app.config import Settings

STDLIB_LEVELS: Final = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARN: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
}
LEVEL_NAMES: Final = {"warning": "warn", "critical": "error", "exception": "error"}
SECRET_FIELDS: Final = frozenset(
    {
        "authorization",
        "cookie",
        "password",
        "currentpassword",
        "newpassword",
        "token",
        "accesstoken",
        "refreshtoken",
        "access_token",
        "refresh_token",
        "code",
        "signature",
    }
)
"""小写比较。"""
MASKED_FIELDS: Final = {"email": mask_email, "phone": mask_phone}
MAX_DEPTH: Final = 5
QUIET_LOGGERS: Final = ("httpx", "httpcore", "urllib3")

_HANDLER_MARK: Final = "_app_structlog_handler"
_user_id: ContextVar[str | None] = ContextVar("log_user_id", default=None)


class _StdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """每次输出时取当前的 sys.stdout。"""

    def __init__(self) -> None:
        super().__init__()

    @property
    def stream(self) -> Any:
        return sys.stdout

    @stream.setter
    def stream(self, _: Any) -> None:
        pass


def bind_user_id(user_id: Any) -> Any:
    return _user_id.set(None if user_id is None else str(user_id))


def reset_user_id(token: Any) -> None:
    _user_id.reset(token)


def add_timestamp(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    event_dict["ts"] = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return event_dict


def normalize_level(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    level = str(event_dict.get("level", "info")).lower()
    event_dict["level"] = LEVEL_NAMES.get(level, level)
    return event_dict


def add_request_context(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    request_id = current_request_id()
    if request_id is not None:
        event_dict.setdefault("request_id", request_id)
    user_id = _user_id.get()
    if user_id is not None:
        event_dict.setdefault("user_id", user_id)
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict.setdefault("trace_id", format(span_context.trace_id, "032x"))
        event_dict.setdefault("span_id", format(span_context.span_id, "016x"))
    return event_dict


def _scrub(value: Any, depth: int) -> Any:
    if depth > MAX_DEPTH:
        return value
    if isinstance(value, list | tuple):
        return [_scrub(item, depth + 1) for item in value]
    if not isinstance(value, Mapping):
        return value
    scrubbed: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key).lower()
        if name in SECRET_FIELDS:
            continue
        mask = MASKED_FIELDS.get(name)
        if mask is not None and isinstance(item, str):
            scrubbed[key] = mask(item)
        else:
            scrubbed[key] = _scrub(item, depth + 1)
    return scrubbed


def scrub_secrets(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """丢弃令牌、密码、验证码字段，邮箱与手机号掩码。"""
    result: EventDict = _scrub(event_dict, 0)
    return result


class _Service:
    def __init__(self, service: str, env: str) -> None:
        self.service = service
        self.env = env

    def __call__(self, _: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service", self.service)
        event_dict.setdefault("env", self.env)
        return event_dict


def _shared_processors(settings: Settings) -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        normalize_level,
        add_timestamp,
        _Service(settings.telemetry.service_name, settings.app.env.value),
        add_request_context,
        scrub_secrets,
        structlog.processors.StackInfoRenderer(),
    ]


def _renderers(log_format: LogFormat) -> list[Processor]:
    if log_format is LogFormat.TEXT:
        return [structlog.dev.ConsoleRenderer(colors=True, timestamp_key="ts")]
    return [
        structlog.processors.format_exc_info,
        structlog.processors.EventRenamer("msg"),
        structlog.processors.JSONRenderer(),
    ]


def configure_logging(settings: Settings) -> None:
    production = settings.app.is_production
    level = STDLIB_LEVELS[settings.log.resolved_level(production)]
    shared = _shared_processors(settings)
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[structlog.stdlib.ExtraAdder(), *shared],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *_renderers(settings.log.resolved_format(production)),
        ],
    )
    handler = _StdoutHandler()
    handler.setFormatter(formatter)
    setattr(handler, _HANDLER_MARK, True)
    root = logging.getLogger()
    for existing in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
