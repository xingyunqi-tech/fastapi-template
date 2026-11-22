"""用户管理（约定 §11.3）：创建、更新、设置角色，以及 `role.lastAdmin` 检查。"""

from typing import TYPE_CHECKING
lazy from uuid import UUID

from app.common.errors import AppError, FieldError, ValidationFailed
from app.identity.sessions.service import SessionService
from app.identity.users.models import UserStatus
from app.identity.users.repository import UserRepository
from app.security.rbac import Role

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.context import AppContext
    from app.common.pagination import SortField
    from app.identity.users.models import UserModel
    from app.identity.users.schemas import (
        CreateUserRequest,
        SetUserRolesRequest,
        UpdateUserRequest,
    )


async def ensure_not_last_admin(repository: UserRepository, user: UserModel) -> None:
    """锁住全部 active admin 的 `user_roles` 行后重新计数；`user` 是唯一的 active admin 时 409。"""
    await repository.active_admin_ids(lock=True)
    admins = await repository.active_admin_ids()
    if user.id in admins and len(admins) == 1:
        raise AppError("role.lastAdmin")


class UsersService:
    def __init__(self, context: AppContext, session: AsyncSession) -> None:
        self.context = context
        self.repository = UserRepository(session)
        self.sessions = SessionService(context, session)

    async def get(self, user_id: UUID) -> UserModel:
        user = await self.repository.find_by_id(user_id)
        if user is None:
            raise AppError("user.notFound")
        return user

    async def find_page(
        self,
        *,
        keyword: str | None,
        status: UserStatus | None,
        role: Role | None,
        sort: Sequence[SortField],
        offset: int,
        limit: int,
    ) -> tuple[list[UserModel], int]:
        return await self.repository.find_page(
            keyword=keyword, status=status, role=role, sort=sort, offset=offset, limit=limit
        )

    async def create(self, body: CreateUserRequest) -> UserModel:
        if body.email is None and body.phone is None:
            raise ValidationFailed(
                [FieldError("email", "required"), FieldError("phone", "required")]
            )
        password_hash = (
            await self.context.passwords.hash_async(body.password) if body.password else None
        )
        return await self.repository.create(
            email=body.email,
            phone=body.phone,
            password_hash=password_hash,
            display_name=body.display_name,
        )

    async def update(self, user_id: UUID, body: UpdateUserRequest) -> UserModel:
        """禁用时先做 `role.lastAdmin` 检查，再吊销其全部会话。"""
        user = await self.get(user_id)
        fields = body.model_fields_set
        disabling = (
            "status" in fields
            and body.status is UserStatus.DISABLED
            and user.status != UserStatus.DISABLED
        )
        if disabling:
            await ensure_not_last_admin(self.repository, user)
        if "display_name" in fields:
            user.display_name = body.display_name
        if "status" in fields and body.status is not None:
            user.status = body.status.value
        if disabling:
            await self.sessions.revoke(user.id)
        return await self.repository.reload(user)

    async def set_roles(self, user_id: UUID, body: SetUserRolesRequest) -> UserModel:
        """未知角色 422 `exists`，缺少 `user` 422 `contains`；移除最后一个 admin 时 409。"""
        errors = [
            FieldError(f"roles[{index}]", "exists")
            for index, code in enumerate(body.roles)
            if code not in Role.__members__.values()
        ]
        if errors:
            raise ValidationFailed(errors)
        if Role.USER not in body.roles:
            raise ValidationFailed([FieldError("roles", "contains", {"value": Role.USER.value})])
        user = await self.get(user_id)
        roles = sorted(set(body.roles))
        if Role.ADMIN in user.roles and Role.ADMIN not in roles:
            await ensure_not_last_admin(self.repository, user)
        await self.repository.set_roles(user, roles)
        await self.sessions.revoke(user.id)
        return await self.repository.reload(user)
