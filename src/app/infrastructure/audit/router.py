"""`GET /api/v1/audit-logs`（`listAuditLogs`，`audit:read`），按时间倒序游标分页。"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import Query
from sqlalchemy import select, tuple_

from app.common.pagination import CursorPage, CursorParams, encode_cursor, invalid_cursor
from app.common.routing import ApiRouter, Auth
from app.common.types import ApiModel, UtcDatetime
from app.infrastructure.audit.models import AuditLogModel
from app.infrastructure.database.session import DbSession
from app.security.rbac import Permission


class AuditLog(ApiModel):
    id: UUID
    actor_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    request_id: str | None
    ip: str | None
    user_agent: str | None
    changes: Any
    created_at: UtcDatetime

    @classmethod
    def from_model(cls, row: AuditLogModel) -> AuditLog:
        return cls(
            id=row.id,
            actor_id=row.actor_id,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            request_id=row.request_id,
            ip=None if row.ip is None else str(row.ip),
            user_agent=row.user_agent,
            changes=row.changes,
            created_at=row.created_at,
        )


class AuditLogCursorPage(CursorPage[AuditLog]):
    pass


router = ApiRouter(prefix="/v1/audit-logs", tags=["Audit"])


@router.endpoint(
    "GET",
    "",
    operation_id="listAuditLogs",
    auth=Auth.JWT,
    permissions=[Permission.AUDIT_READ],
    summary="审计日志",
)
async def list_audit_logs(
    params: Annotated[CursorParams, Query()], session: DbSession
) -> AuditLogCursorPage:
    query = select(AuditLogModel)
    decoded = params.decoded(str, str)
    if decoded is not None:
        try:
            position = (datetime.fromisoformat(decoded[0]), UUID(decoded[1]))
        except ValueError:
            raise invalid_cursor() from None
        if position[0].tzinfo is None:
            raise invalid_cursor()
        query = query.where(tuple_(AuditLogModel.created_at, AuditLogModel.id) < tuple_(*position))
    rows = list(
        await session.scalars(
            query.order_by(AuditLogModel.created_at.desc(), AuditLogModel.id.desc()).limit(
                params.limit + 1
            )
        )
    )
    more = len(rows) > params.limit
    rows = rows[: params.limit]
    next_cursor = encode_cursor(rows[-1].created_at.isoformat(), str(rows[-1].id)) if more else None
    return AuditLogCursorPage(items=[AuditLog.from_model(r) for r in rows], next_cursor=next_cursor)
