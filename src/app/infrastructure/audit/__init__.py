"""审计日志：路由声明 `audit=Audit(...)` 时，请求成功后在独立事务中写 `audit_logs`。"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

lazy from fastapi import Request

from app.common.client import client_info
from app.common.middleware import current_request_id
from app.infrastructure.audit.models import AuditLogModel
lazy from app.common.context import AppContext

logger = logging.getLogger("app.audit")

SENSITIVE_KEY_PARTS: Final = ("password", "secret", "token", "code", "key")
REDACTED: Final = "[REDACTED]"
MAX_DEPTH: Final = 10


@dataclass(frozen=True, slots=True)
class Audit:
    """`resource_id` 先取路径参数 `id_param`，再取响应 `data.id`。"""

    action: str
    resource: str
    id_param: str = "id"
    include_body: bool = True


def is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(part in lower for part in SENSITIVE_KEY_PARTS)


def sanitize(value: Any, depth: int = 0) -> Any:
    """敏感键替换为 `[REDACTED]`，超过 10 层截断。"""
    if depth > MAX_DEPTH:
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if is_sensitive_key(str(key)) else sanitize(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [sanitize(item, depth + 1) for item in value]
    return value


def resource_id_of(params: Mapping[str, Any], id_param: str, response: Any) -> str | None:
    value = params.get(id_param)
    if value is None and isinstance(response, Mapping):
        value = response.get("id")
    if value is None or isinstance(value, bool):
        return None
    return str(value)


def _actor(request: Request) -> str | None:
    principal = getattr(request.state, "principal", None)
    return None if principal is None else str(principal.sub)


async def write_audit_log(
    context: AppContext,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None,
    actor_id: str | None,
    changes: Any,
    request: Request | None = None,
) -> None:
    client = client_info(request, context.settings.app.trusted_proxies) if request else None
    async with context.database.transaction() as session:
        session.add(
            AuditLogModel(
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=current_request_id(),
                ip=client.ip if client else None,
                user_agent=client.user_agent if client else None,
                changes=sanitize(changes),
            )
        )


async def record_audit(request: Request, audit: Audit, body: Any, response: Any) -> None:
    """失败只记日志，不影响请求。"""
    try:
        await write_audit_log(
            request.app.state.context,
            action=audit.action,
            resource_type=audit.resource,
            resource_id=resource_id_of(request.path_params, audit.id_param, response),
            actor_id=_actor(request),
            changes=body if audit.include_body else None,
            request=request,
        )
    except Exception:
        logger.exception("Failed to write audit log %s", audit.action)
