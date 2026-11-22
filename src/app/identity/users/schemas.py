"""约定 §11.3 / §11.4 的用户、角色、权限对象与管理请求。"""

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

from app.common.pagination import Page
from app.common.types import ApiModel, Email, Phone, UtcDatetime
from app.identity.users.models import UserStatus
from app.security.passwords import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH

if TYPE_CHECKING:
    from app.identity.users.models import UserModel
    from app.modules.files.urls import FileUrls

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Password = Annotated[
    str, StringConstraints(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
]
RoleCode = Annotated[str, StringConstraints(max_length=32)]


class User(ApiModel):
    id: UUID
    email: str | None
    email_verified: bool
    phone: str | None
    display_name: str | None
    avatar_url: str | None
    status: UserStatus
    roles: list[str]
    has_password: bool
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @classmethod
    def from_model(cls, user: UserModel, urls: FileUrls) -> User:
        """`user` 须已加载头像。"""
        return cls(
            id=user.id,
            email=user.email,
            email_verified=user.email_verified_at is not None,
            phone=user.phone,
            display_name=user.display_name,
            avatar_url=urls.build(user.avatar) if user.avatar is not None else None,
            status=UserStatus(user.status),
            roles=user.roles,
            has_password=user.password_hash is not None,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )


class UserPage(Page[User]):
    pass


class CreateUserRequest(ApiModel):
    email: Email | None = None
    phone: Phone | None = None
    password: Password | None = None
    display_name: DisplayName | None = None


class UpdateUserRequest(ApiModel):
    display_name: DisplayName | None = None
    status: UserStatus | None = None


class SetUserRolesRequest(ApiModel):
    roles: list[RoleCode] = Field(max_length=16)


class Role(ApiModel):
    code: str
    name: str
    permissions: list[str]


class PermissionInfo(ApiModel):
    code: str
