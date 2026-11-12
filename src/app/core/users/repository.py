from dataclasses import dataclass
from datetime import date
from typing import Any, Final
lazy from collections.abc import Sequence
lazy from uuid import UUID

from sqlalchemy import ColumnElement, Integer, Select, String, func, select, update
from sqlalchemy.orm import joinedload
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import not_found
from app.common.validation.js import to_string
from app.core.users.models import UserModel
from app.infrastructure.database.session import affected_rows

_UPDATABLE: Final = {
    "email": UserModel.email,
    "password": UserModel.password,
    "provider": UserModel.provider,
    "firstName": UserModel.first_name,
    "lastName": UserModel.last_name,
    "phone": UserModel.phone,
    "nickname": UserModel.nickname,
    "gender": UserModel.gender,
    "birthday": UserModel.birthday,
    "wechatOpenId": UserModel.wechat_open_id,
    "wechatUnionId": UserModel.wechat_union_id,
    "alipayOpenId": UserModel.alipay_open_id,
}
_SORTABLE: Final = {
    **_UPDATABLE,
    "id": UserModel.id,
    "createdAt": UserModel.created_at,
    "updatedAt": UserModel.updated_at,
    "deletedAt": UserModel.deleted_at,
}
UNSET: Final = object()


@dataclass(frozen=True, slots=True)
class Sort:
    order_by: str
    order: str


def _coerce(attribute: Any, value: Any) -> Any:
    """与 pg 驱动一致：数字 / 布尔写入字符串列时转为 JS 字符串，数字字符串写入整数列时转为整数。"""
    if value is None or value is UNSET:
        return value
    column_type = attribute.property.columns[0].type
    if isinstance(column_type, String) and not isinstance(value, str):
        return to_string(value)
    if isinstance(column_type, Integer) and isinstance(value, str):
        return int(value)
    return value


def _with_relations(statement: Select[Any]) -> Select[Any]:
    return statement.options(
        joinedload(UserModel.role), joinedload(UserModel.status), joinedload(UserModel.photo)
    )


def _active() -> ColumnElement[bool]:
    return UserModel.deleted_at.is_(None)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _one(self, *conditions: Any) -> UserModel | None:
        statement = _with_relations(select(UserModel).where(_active(), *conditions))
        return (await self.session.scalars(statement)).unique().one_or_none()

    async def find_by_id(self, user_id: int) -> UserModel | None:
        return await self._one(UserModel.id == user_id)

    async def find_by_email(self, email: str | None) -> UserModel | None:
        return await self._one(UserModel.email == email) if email else None

    async def find_by_phone(self, phone: str | None) -> UserModel | None:
        return await self._one(UserModel.phone == phone) if phone else None

    async def find_by_wechat_open_id(self, open_id: str | None) -> UserModel | None:
        return await self._one(UserModel.wechat_open_id == open_id) if open_id else None

    async def find_by_alipay_open_id(self, open_id: str | None) -> UserModel | None:
        return await self._one(UserModel.alipay_open_id == open_id) if open_id else None

    async def bind_phone_if_empty(self, user_id: int, phone: str) -> bool:
        result = await self.session.execute(
            update(UserModel)
            .where(UserModel.id == user_id, UserModel.phone.is_(None), _active())
            .values(phone=phone, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        self.session.expire_all()
        return affected_rows(result) > 0

    async def bind_wechat_if_empty(
        self, user_id: int, open_id: str | None, union_id: str | None = None
    ) -> bool:
        values: dict[str, Any] = {}
        if open_id is not None:
            values["wechat_open_id"] = open_id
        if union_id:
            values["wechat_union_id"] = union_id
        result = await self.session.execute(
            update(UserModel)
            .where(UserModel.id == user_id, UserModel.wechat_open_id.is_(None), _active())
            .values(**values, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        self.session.expire_all()
        return affected_rows(result) > 0

    async def find_by_ids(self, ids: Sequence[int]) -> list[UserModel]:
        statement = _with_relations(select(UserModel).where(_active(), UserModel.id.in_(ids)))
        return list((await self.session.scalars(statement)).unique())

    async def find_many(
        self,
        *,
        role_ids: Sequence[int] | None,
        sort: Sequence[Sort] | None,
        page: int,
        limit: int,
    ) -> list[UserModel]:
        statement = select(UserModel).where(_active())
        if role_ids:
            statement = statement.where(UserModel.role_id.in_(role_ids))
        ordered_by_id = False
        for item in sort or ():
            column = _SORTABLE.get(item.order_by)
            if column is None:
                raise LookupError(f'Property "{item.order_by}" was not found in "UserEntity"')
            ordered_by_id = ordered_by_id or column is UserModel.id
            statement = statement.order_by(column.desc() if item.order == "DESC" else column.asc())
        if not ordered_by_id:
            statement = statement.order_by(UserModel.id.asc())
        statement = _with_relations(statement.offset((page - 1) * limit).limit(limit))
        return list((await self.session.scalars(statement)).unique())

    async def create(
        self,
        *,
        values: dict[str, Any],
        photo_id: UUID | None,
        role_id: int | None,
        status_id: int | None,
    ) -> UserModel:
        user = UserModel(
            **{
                _UPDATABLE[key].key: _coerce(_UPDATABLE[key], value)
                for key, value in values.items()
            },
            photo_id=photo_id,
            role_id=role_id,
            status_id=status_id,
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user, ["created_at", "updated_at", "deleted_at", "gender"])
        return user

    async def update(
        self,
        user_id: int,
        *,
        values: dict[str, Any],
        photo_id: UUID | object | None = UNSET,
        role_id: int | None = None,
        status_id: int | None = None,
    ) -> UserModel:
        changes: dict[str, Any] = {
            _UPDATABLE[key].key: _coerce(_UPDATABLE[key], value)
            for key, value in values.items()
            if value is not UNSET
        }
        if photo_id is not UNSET:
            changes["photo_id"] = photo_id
        if role_id is not None:
            changes["role_id"] = role_id
        if status_id is not None:
            changes["status_id"] = status_id
        if changes:
            result = await self.session.execute(
                update(UserModel)
                .where(UserModel.id == user_id, _active())
                .values(**changes, updated_at=func.now())
                .execution_options(synchronize_session=False)
            )
            if affected_rows(result) == 0:
                raise not_found("User not found")
        self.session.expire_all()
        user = await self._one(UserModel.id == user_id)
        if user is None:
            raise not_found("User not found")
        return user

    async def find_deleted_by_id(self, user_id: int) -> UserModel | None:
        return await self.session.scalar(
            select(UserModel).where(UserModel.id == user_id, UserModel.deleted_at.is_not(None))
        )

    async def detach_photo(self, user_id: int) -> None:
        await self.session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(photo_id=None, updated_at=func.now())
        )

    async def remove(self, user_id: int) -> None:
        await self.session.execute(
            update(UserModel)
            .where(UserModel.id == user_id)
            .values(
                email=None,
                phone=None,
                wechat_open_id=None,
                wechat_union_id=None,
                alipay_open_id=None,
                updated_at=func.now(),
            )
        )
        await self.session.execute(
            update(UserModel)
            .where(UserModel.id == user_id, _active())
            .values(deleted_at=func.now(), updated_at=func.now())
        )


def parse_birthday(value: Any) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(str(value)[:10])
