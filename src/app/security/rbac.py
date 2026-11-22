"""约定 §5.5 角色与权限：平台常量与本服务私有权限码。"""

from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable


class Role(StrEnum):
    ADMIN = "admin"
    USER = "user"


ROLE_NAMES: Final = {Role.ADMIN: "Administrator", Role.USER: "User"}


class Permission(StrEnum):
    USER_READ = "user:read"
    USER_WRITE = "user:write"
    ROLE_READ = "role:read"
    ROLE_WRITE = "role:write"
    AUDIT_READ = "audit:read"


_GRANTS: dict[str, set[Role]] = {permission.value: set() for permission in Permission}


def register(permission: str, *, roles: Iterable[Role]) -> str:
    """登记本服务私有权限码并授予 `roles`；`admin` 自动拥有。"""
    _GRANTS.setdefault(permission, set()).update(roles)
    return permission


def permission_codes() -> list[str]:
    return list(_GRANTS)


def permissions_of(role: Role) -> list[str]:
    if role is Role.ADMIN:
        return permission_codes()
    return [permission for permission, roles in _GRANTS.items() if role in roles]


def permissions_for(roles: Iterable[str]) -> set[str]:
    granted: set[str] = set()
    for code in roles:
        if code in Role.__members__.values():
            granted.update(permissions_of(Role(code)))
    return granted
