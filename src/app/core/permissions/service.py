"""RBAC：权限枚举、默认角色映射、角色权限缓存（5 分钟，库为空回落默认值）。"""

from enum import IntEnum, StrEnum
from typing import Final

from sqlalchemy import select
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions.models import RolePermissionModel
lazy from app.infrastructure.cache import Cache


class RoleId(IntEnum):
    ADMIN = 1
    USER = 2
    EDITOR = 3
    REVIEWER = 4


class StatusId(IntEnum):
    ACTIVE = 1
    INACTIVE = 2


class Permission(StrEnum):
    USER_READ = "user:read"
    USER_CREATE = "user:create"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"
    USER_LIST = "user:list"
    ADDRESS_READ = "address:read"
    ADDRESS_CREATE = "address:create"
    ADDRESS_UPDATE = "address:update"
    ADDRESS_DELETE = "address:delete"
    FAVORITE_READ = "favorite:read"
    FAVORITE_CREATE = "favorite:create"
    FAVORITE_DELETE = "favorite:delete"
    FEEDBACK_READ = "feedback:read"
    FEEDBACK_CREATE = "feedback:create"
    FEEDBACK_LIST = "feedback:list"
    FILE_UPLOAD = "file:upload"
    FILE_READ = "file:read"
    FILE_DELETE = "file:delete"
    SESSION_READ = "session:read"
    SESSION_DELETE = "session:delete"
    SYSTEM_CONFIG = "system:config"
    SYSTEM_METRICS = "system:metrics"
    PAYMENT_REFUND = "payment:refund"
    AUDIT_READ = "audit:read"
    AUDIT_LIST = "audit:list"
    PRACTICE_WRITE = "practice:write"
    AI_CHAT = "ai:chat"
    AI_GRADING = "ai:grading"
    CONTENT_MANAGE = "content:manage"
    CONTENT_REVIEW = "content:review"
    AI_GENERATE = "ai:generate"
    AI_ADMIN = "ai:admin"


P = Permission
DEFAULT_ROLE_PERMISSIONS: Final[dict[int, list[str]]] = {
    RoleId.ADMIN: [p.value for p in Permission],
    RoleId.USER: [
        p.value
        for p in (
            P.ADDRESS_READ,
            P.ADDRESS_CREATE,
            P.ADDRESS_UPDATE,
            P.ADDRESS_DELETE,
            P.FAVORITE_READ,
            P.FAVORITE_CREATE,
            P.FAVORITE_DELETE,
            P.FEEDBACK_READ,
            P.FEEDBACK_CREATE,
            P.FILE_UPLOAD,
            P.FILE_READ,
            P.SESSION_READ,
            P.PRACTICE_WRITE,
            P.AI_CHAT,
            P.AI_GRADING,
        )
    ],
    RoleId.EDITOR: [p.value for p in (P.FILE_UPLOAD, P.FILE_READ, P.CONTENT_MANAGE, P.AI_GENERATE)],
    RoleId.REVIEWER: [
        p.value for p in (P.FILE_UPLOAD, P.FILE_READ, P.CONTENT_MANAGE, P.CONTENT_REVIEW)
    ],
}
ROLE_PERMISSIONS_CACHE_KEY: Final = "permissions:v2:role-map"
CACHE_TTL_MS: Final = 5 * 60 * 1000


class PermissionsService:
    def __init__(self, session: AsyncSession, cache: Cache) -> None:
        self.session = session
        self.cache = cache

    async def permissions_for_role(self, role_id: int) -> list[str]:
        if role_id == RoleId.ADMIN:
            return [p.value for p in Permission]
        role_map = await self._role_map()
        return role_map.get(str(role_id), [])

    async def _role_map(self) -> dict[str, list[str]]:
        cached = await self.cache.get(ROLE_PERMISSIONS_CACHE_KEY)
        if cached:
            return dict(cached)
        rows = (await self.session.execute(select(RolePermissionModel))).scalars().all()
        stored: dict[str, list[str]] = {}
        for row in rows:
            stored.setdefault(str(row.role_id), []).append(row.permission_id)
        role_map = stored or {str(k): v for k, v in DEFAULT_ROLE_PERMISSIONS.items()}
        await self.cache.set(ROLE_PERMISSIONS_CACHE_KEY, role_map, CACHE_TTL_MS)
        return role_map

    async def refresh_cache(self) -> None:
        await self.cache.delete(ROLE_PERMISSIONS_CACHE_KEY)
