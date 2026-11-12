"""审计日志：写 `audit_logs` 并脱敏；路由声明 `audit=Auditable(...)` 时请求成功后自动记录。"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Final

lazy from fastapi import Request

from app.common.client import client_ip as express_client_ip
from app.common.middleware import current_request_id
from app.common.security import InvalidTokenError, Jwt, TokenExpiredError
from app.infrastructure.audit.models import AuditLogModel
lazy from app.common.routing import Auth
lazy from app.infrastructure.database.session import Database

logger = logging.getLogger("app.audit")

SENSITIVE_KEY_PARTS: Final = (
    "password",
    "secret",
    "token",
    "apikey",
    "privatekey",
    "sessionkey",
    "accesskey",
)
SENSITIVE_KEYS: Final = ("hash",)
REDACTED: Final = "[REDACTED]"
MAX_DEPTH: Final = 10


class AuditAction(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"  # noqa: S105
    PASSWORD_RESET = "PASSWORD_RESET"  # noqa: S105
    PERMISSION_CHANGE = "PERMISSION_CHANGE"
    SUBMIT = "SUBMIT"
    PUBLISH = "PUBLISH"
    OFFLINE = "OFFLINE"
    REJECT = "REJECT"
    COPY = "COPY"
    MOVE = "MOVE"


@dataclass(frozen=True, slots=True)
class Auditable:
    """nestjs `@Auditable`：entity_id_from=param 先取路由参数再取响应 id，response 相反。"""

    action: AuditAction
    entity_type: str
    id_param: str | tuple[str, ...] = "id"
    entity_id_from: str = "param"
    include_body: bool = True
    include_response: bool = False
    summarize_body: Callable[[Any, dict[str, str]], dict[str, Any]] | None = None


def is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return lower in SENSITIVE_KEYS or any(part in lower for part in SENSITIVE_KEY_PARTS)


def sanitize(value: Any, depth: int = 0, seen: frozenset[int] = frozenset()) -> Any:
    """递归脱敏：敏感键替换为 `[REDACTED]`，超过 10 层截断，循环引用标记。"""
    if value is None:
        return None
    if depth > MAX_DEPTH:
        return {"__truncated": True}
    if id(value) in seen:
        return {"__circular": True}
    seen = seen | {id(value)}
    items = value.items() if isinstance(value, Mapping) else enumerate(value)
    return {
        str(key): REDACTED if is_sensitive_key(str(key)) else _sanitize_value(item, depth, seen)
        for key, item in items
    }


def _sanitize_value(value: Any, depth: int, seen: frozenset[int]) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_sanitize_value(item, depth + 1, seen) for item in value]
    if isinstance(value, Mapping):
        return sanitize(value, depth + 1, seen)
    return value


def client_ip(request: Request | None) -> str | None:
    """X-Forwarded-For 的第一个地址，否则为 req.ip。"""
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return express_client_ip(request, request.app.state.settings.app.trust_proxy)


class AuditService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def log(
        self,
        *,
        action: AuditAction | str,
        entity_type: str,
        entity_id: str | int,
        user_id: str | int | None = None,
        old_value: Mapping[str, Any] | list[Any] | None = None,
        new_value: Mapping[str, Any] | list[Any] | None = None,
        request: Request | None = None,
    ) -> AuditLogModel:
        async with self.database.transaction() as session:
            model = AuditLogModel(
                user_id=str(user_id) if user_id is not None else None,
                action=str(action),
                entity_type=entity_type,
                entity_id=str(entity_id),
                old_value=sanitize(old_value),
                new_value=sanitize(new_value),
                ip_address=client_ip(request),
                user_agent=request.headers.get("user-agent") if request is not None else None,
                request_id=current_request_id() if request is not None else None,
            )
            session.add(model)
            await session.flush()
        logger.debug(
            "Audit log created: %s on %s:%s by user %s",
            action,
            entity_type,
            entity_id,
            user_id if user_id is not None else "anonymous",
        )
        return model


def _unwrap(response: Any) -> Any:
    if (
        isinstance(response, Mapping)
        and isinstance(response.get("code"), int)
        and "message" in response
        and "data" in response
    ):
        return response["data"]
    return response


def _response_id(response: Any) -> str | None:
    data = _unwrap(response)
    value = data.get("id") if isinstance(data, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    return str(value)


def _param_id(params: Mapping[str, Any], id_param: str | tuple[str, ...]) -> str | None:
    names = (id_param,) if isinstance(id_param, str) else id_param
    values = [params.get(name) for name in names]
    if any(value is None or value == "" for value in values):
        return None
    return ":".join(str(value) for value in values)


def _user_id(request: Request) -> Any:
    """鉴权路由的 JWT 用户 id；公开路由即使携带 token 也为 None。"""
    if getattr(request.scope.get("route"), "auth", Auth.PUBLIC) in {None, Auth.PUBLIC}:
        return None
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    secret = request.app.state.settings.auth.secret.get_secret_value()
    try:
        return Jwt.verify(token.strip(), secret).get("id")
    except InvalidTokenError, TokenExpiredError:
        return None


async def record_audit(request: Request, options: Auditable, body: Any, response: Any) -> None:
    """路由成功返回后写审计日志；失败只记日志，不影响请求。"""
    try:
        params = dict(request.path_params)
        param_id = _param_id(params, options.id_param)
        response_id = _response_id(response)
        if options.entity_id_from == "response":
            entity_id = response_id or param_id or "unknown"
        else:
            entity_id = param_id or response_id or "unknown"
        recorded: Any = None
        if options.summarize_body is not None:
            recorded = options.summarize_body(body, params)
        elif options.include_body and isinstance(body, Mapping):
            recorded = dict(body)
        value = _unwrap(response)
        new_value = (
            value if options.include_response and isinstance(value, Mapping | list) else recorded
        )
        await AuditService(request.app.state.context.database).log(
            action=options.action,
            entity_type=options.entity_type,
            entity_id=entity_id,
            user_id=_user_id(request),
            new_value=new_value,
            request=request,
        )
    except Exception:
        logger.exception("Failed to create audit log")
