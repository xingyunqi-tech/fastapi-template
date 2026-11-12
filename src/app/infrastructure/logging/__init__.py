"""structlog：生产 JSON、开发彩色单行；标准库 logging 统一经 structlog 渲染。"""

import logging
import sys
from contextvars import ContextVar
from typing import Any, Final

import structlog
from opentelemetry import trace
lazy from structlog.typing import EventDict, Processor, WrappedLogger

from app.common.middleware import current_request_id
from app.config import LogLevel
lazy from app.config import Settings

REDACTED: Final = "[REDACTED]"
REDACT_HEADERS: Final = frozenset({"authorization", "cookie"})
REDACT_BODY: Final = frozenset({"password", "oldPassword", "newPassword", "code", "phone", "email"})
REDACT_QUERY: Final = frozenset({"hash", "token"})
SILENT_LEVEL: Final = logging.CRITICAL + 10
STDLIB_LEVELS: Final = {
    LogLevel.FATAL: logging.CRITICAL,
    LogLevel.ERROR: logging.ERROR,
    LogLevel.WARN: logging.WARNING,
    LogLevel.INFO: logging.INFO,
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.TRACE: 5,
    LogLevel.SILENT: SILENT_LEVEL,
}
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


def _redact(values: Any, keys: frozenset[str]) -> Any:
    if not isinstance(values, dict):
        return values
    return {k: REDACTED if k in keys else v for k, v in values.items()}


def redact_request(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """nestjs pino `redact.paths`：req.headers / req.body / req.query 下的敏感字段。"""
    request = event_dict.get("req")
    if isinstance(request, dict):
        event_dict["req"] = {
            **request,
            **{
                part: _redact(request[part], keys)
                for part, keys in (
                    ("headers", REDACT_HEADERS),
                    ("body", REDACT_BODY),
                    ("query", REDACT_QUERY),
                )
                if part in request
            },
        }
    return event_dict


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="time"),
        add_request_context,
        redact_request,
        structlog.processors.StackInfoRenderer(),
    ]


def _renderers(pretty: bool) -> list[Processor]:
    if pretty:
        return [structlog.dev.ConsoleRenderer(colors=True)]
    return [
        structlog.processors.format_exc_info,
        structlog.processors.EventRenamer("msg"),
        structlog.processors.JSONRenderer(),
    ]


def configure_logging(settings: Settings) -> None:
    production = settings.app.is_production
    level = STDLIB_LEVELS[settings.log.resolved_level(production)]
    shared = _shared_processors()
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*shared, structlog.stdlib.ExtraAdder()],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *_renderers(settings.log.resolved_pretty(production)),
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
