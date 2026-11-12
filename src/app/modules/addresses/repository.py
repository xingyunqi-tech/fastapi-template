from dataclasses import dataclass
from typing import Any, Final
lazy from collections.abc import Mapping, Sequence

from sqlalchemy import ColumnElement, delete, func, select, text, update
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import offset_limit
from app.modules.addresses.models import AddressModel

UPDATABLE: Final = {
    "name": AddressModel.name,
    "phone": AddressModel.phone,
    "provinceCode": AddressModel.province_code,
    "cityCode": AddressModel.city_code,
    "districtCode": AddressModel.district_code,
    "province": AddressModel.province,
    "city": AddressModel.city,
    "district": AddressModel.district,
    "address": AddressModel.address,
    "isDefault": AddressModel.is_default,
}
SORTABLE: Final = {
    "id": AddressModel.id,
    "name": AddressModel.name,
    "isDefault": AddressModel.is_default,
    "createdAt": AddressModel.created_at,
    "updatedAt": AddressModel.updated_at,
}
_DEFAULT_ORDER: Final = (("isDefault", "DESC"), ("createdAt", "DESC"))
_FILTERS: Final = {
    "province": AddressModel.province,
    "city": AddressModel.city,
    "provinceCode": AddressModel.province_code,
    "cityCode": AddressModel.city_code,
    "districtCode": AddressModel.district_code,
}


@dataclass(frozen=True, slots=True)
class Sort:
    order_by: str
    order: str


def _active() -> ColumnElement[bool]:
    return AddressModel.deleted_at.is_(None)


def _where(user_id: int, filters: Mapping[str, Any] | None) -> list[ColumnElement[bool]]:
    conditions = [AddressModel.user_id == user_id, _active()]
    for key, column in _FILTERS.items():
        value = (filters or {}).get(key)
        if value:
            conditions.append(column == value)
    return conditions


def _order(sort: Sequence[Sort] | None) -> list[ColumnElement[Any]]:
    """排序项在前，默认排序（isDefault、createdAt 倒序）补在未出现的字段上。"""
    order: dict[str, str] = {}
    for item in sort or ():
        order[item.order_by] = item.order
    if order:
        for key, direction in _DEFAULT_ORDER:
            order.setdefault(key, direction)
    else:
        order = dict(_DEFAULT_ORDER)
    return [
        SORTABLE[key].desc() if direction == "DESC" else SORTABLE[key].asc()
        for key, direction in order.items()
    ]


class AddressRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _lock_user(self, user_id: int) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('address'), :user_id)"),
            {"user_id": user_id},
        )

    async def create_within_limit(
        self, values: dict[str, Any], limit: int
    ) -> tuple[AddressModel, int] | None:
        """同一用户串行创建；已有 limit 个地址时返回 None，否则返回新地址与创建前的数量。"""
        user_id = int(values["user_id"])
        await self._lock_user(user_id)
        previous = await self.session.scalar(
            select(func.count()).select_from(AddressModel).where(*_where(user_id, None))
        )
        if (previous or 0) >= limit:
            return None
        address = AddressModel(**values)
        self.session.add(address)
        await self.session.flush()
        await self.session.refresh(address)
        return address, int(previous or 0)

    async def find_many(
        self,
        user_id: int,
        *,
        filters: Mapping[str, Any] | None = None,
        sort: Sequence[Sort] | None = None,
        page: float,
        limit: float,
    ) -> list[AddressModel]:
        offset, size = offset_limit(page, limit)
        statement = (
            select(AddressModel)
            .where(*_where(user_id, filters))
            .order_by(*_order(sort))
            .offset(offset)
            .limit(size)
        )
        return list(await self.session.scalars(statement))

    async def count(self, user_id: int, filters: Mapping[str, Any] | None) -> int:
        total = await self.session.scalar(
            select(func.count()).select_from(AddressModel).where(*_where(user_id, filters))
        )
        return int(total or 0)

    async def find_by_id_and_user(self, address_id: int, user_id: int) -> AddressModel | None:
        return await self.session.scalar(
            select(AddressModel).where(
                AddressModel.id == address_id, AddressModel.user_id == user_id, _active()
            )
        )

    async def find_default(self, user_id: int) -> AddressModel | None:
        return await self.session.scalar(
            select(AddressModel)
            .where(AddressModel.user_id == user_id, AddressModel.is_default.is_(True), _active())
            .limit(1)
        )

    async def update(self, address_id: int, changes: dict[str, Any]) -> AddressModel | None:
        values = {UPDATABLE[key].key: value for key, value in changes.items()}
        if values:
            await self.session.execute(
                update(AddressModel)
                .where(AddressModel.id == address_id, _active())
                .values(**values, updated_at=func.now())
                .execution_options(synchronize_session=False)
            )
        self.session.expire_all()
        return await self.session.scalar(
            select(AddressModel).where(AddressModel.id == address_id, _active())
        )

    async def mark_default(self, address_id: int, user_id: int) -> None:
        """先清除该用户其它默认地址再设置目标，满足每个用户最多一个默认地址的唯一索引。"""
        await self._lock_user(user_id)
        params = {"id": address_id, "user_id": user_id}
        target = await self.session.execute(
            text(
                'SELECT 1 FROM "address" WHERE "id" = :id AND "userId" = :user_id '
                'AND "deletedAt" IS NULL'
            ),
            params,
        )
        if target.first() is None:
            return
        await self.session.execute(
            text(
                'UPDATE "address" SET "isDefault" = false WHERE "userId" = :user_id '
                'AND "isDefault" AND "id" <> :id AND "deletedAt" IS NULL'
            ),
            params,
        )
        await self.session.execute(
            text(
                'UPDATE "address" SET "isDefault" = true WHERE "id" = :id '
                'AND "userId" = :user_id AND "deletedAt" IS NULL'
            ),
            params,
        )

    async def refresh(self, address: AddressModel) -> None:
        await self.session.refresh(address)

    async def soft_delete(self, address_id: int) -> None:
        await self.session.execute(
            update(AddressModel)
            .where(AddressModel.id == address_id, _active())
            .values(deleted_at=func.now(), updated_at=func.now())
            .execution_options(synchronize_session=False)
        )

    async def remove_by_user(self, user_id: int) -> None:
        await self.session.execute(delete(AddressModel).where(AddressModel.user_id == user_id))
