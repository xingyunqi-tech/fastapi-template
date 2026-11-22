from typing import Final
lazy from collections.abc import Sequence
lazy from datetime import datetime
lazy from uuid import UUID

from sqlalchemy import ColumnElement, Select, delete, func, or_, select, update
from sqlalchemy.orm import selectinload
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.auth.models import VerificationTokenModel
from app.identity.users.models import UserModel, UserRoleModel, UserStatus
from app.security.rbac import Role
lazy from app.common.pagination import SortField

SORTABLE: Final = {"createdAt": UserModel.created_at, "email": UserModel.email}


def _active() -> ColumnElement[bool]:
    return UserModel.deleted_at.is_(None)


def _loaded(statement: Select[UserModel]) -> Select[UserModel]:
    return statement.options(selectinload(UserModel.avatar))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_by_id(self, user_id: UUID) -> UserModel | None:
        return await self.session.scalar(
            _loaded(select(UserModel).where(UserModel.id == user_id, _active()))
        )

    async def find_by_email(self, email: str) -> UserModel | None:
        return await self.session.scalar(
            _loaded(select(UserModel).where(UserModel.email == email, _active()))
        )

    async def create(
        self,
        *,
        email: str | None,
        phone: str | None,
        password_hash: str | None,
        display_name: str | None,
    ) -> UserModel:
        user = UserModel(
            email=email,
            phone=phone,
            password_hash=password_hash,
            display_name=display_name,
            status=UserStatus.ACTIVE.value,
            role_rows=[UserRoleModel(role=Role.USER.value)],
        )
        self.session.add(user)
        await self.session.flush()
        return await self.reload(user)

    async def reload(self, user: UserModel) -> UserModel:
        """写入后重新读取（含服务端默认值与头像）。"""
        await self.session.flush()
        statement = _loaded(select(UserModel).where(UserModel.id == user.id))
        return (
            await self.session.scalars(statement.execution_options(populate_existing=True))
        ).one()

    def _filtered(
        self, keyword: str | None, status: UserStatus | None, role: Role | None
    ) -> list[ColumnElement[bool]]:
        conditions = [_active()]
        if keyword:
            pattern = f"%{_escape_like(keyword)}%"
            conditions.append(
                or_(
                    UserModel.email.ilike(pattern, escape="\\"),
                    UserModel.phone.ilike(pattern, escape="\\"),
                    UserModel.display_name.ilike(pattern, escape="\\"),
                )
            )
        if status is not None:
            conditions.append(UserModel.status == status.value)
        if role is not None:
            conditions.append(UserModel.role_rows.any(UserRoleModel.role == role.value))
        return conditions

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
        conditions = self._filtered(keyword, status, role)
        order = [
            SORTABLE[field.field].desc().nulls_last()
            if field.descending
            else SORTABLE[field.field].asc().nulls_last()
            for field in sort
        ]
        statement = (
            _loaded(select(UserModel).where(*conditions))
            .order_by(*order, UserModel.id)
            .offset(offset)
            .limit(limit)
        )
        users = list(await self.session.scalars(statement))
        total = await self.session.scalar(
            select(func.count()).select_from(UserModel).where(*conditions)
        )
        return users, int(total or 0)

    async def active_admin_ids(self, *, lock: bool = False) -> set[UUID]:
        """状态为 active 的 admin；`lock` 时对其 `user_roles` 行 `SELECT … FOR UPDATE`。"""
        statement = (
            select(UserRoleModel.user_id)
            .join(UserModel, UserModel.id == UserRoleModel.user_id)
            .where(
                UserRoleModel.role == Role.ADMIN.value,
                UserModel.status == UserStatus.ACTIVE.value,
                _active(),
            )
        )
        if lock:
            statement = statement.with_for_update(of=UserRoleModel)
        return set(await self.session.scalars(statement))

    async def rehash_password(self, user: UserModel, current: str, new: str) -> None:
        """仅当库中仍为 `current` 时替换，避免覆盖并发修改的密码。"""
        await self.session.execute(
            update(UserModel)
            .where(UserModel.id == user.id, UserModel.password_hash == current)
            .values(password_hash=new)
            .execution_options(synchronize_session=False)
        )

    async def set_roles(self, user: UserModel, roles: Sequence[str]) -> None:
        current = {row.role: row for row in user.role_rows}
        user.role_rows = [current.get(role) or UserRoleModel(role=role) for role in roles]
        await self.session.flush()

    async def soft_delete(self, user: UserModel, now: datetime) -> None:
        """软删除并立即释放邮箱、手机号，删除角色与一次性令牌。"""
        user.deleted_at = now
        user.email = None
        user.phone = None
        user.password_hash = None
        user.avatar_file_id = None
        user.role_rows = []
        await self.session.execute(
            delete(VerificationTokenModel).where(VerificationTokenModel.user_id == user.id)
        )
        await self.session.flush()
