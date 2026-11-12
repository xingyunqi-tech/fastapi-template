from typing import Any

from sqlalchemy import func, select, update
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.config.models import ConfigModel


class ConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_by_key(self, key: str) -> ConfigModel | None:
        return await self.session.scalar(select(ConfigModel).where(ConfigModel.key == key))

    async def upsert(self, key: str, value: dict[str, Any], is_public: bool | None) -> ConfigModel:
        """已存在时只在取值变化时写入并刷新 updatedAt（TypeORM save 的脏检查）。"""
        entity = await self.find_by_key(key)
        if entity is None:
            entity = ConfigModel(key=key, value=value, is_public=bool(is_public))
            self.session.add(entity)
            await self.session.flush()
            await self.session.refresh(entity)
            return entity
        changes: dict[str, Any] = {}
        if entity.value != value:
            changes["value"] = value
        if is_public is not None and entity.is_public != is_public:
            changes["is_public"] = is_public
        if changes:
            statement = (
                update(ConfigModel)
                .where(ConfigModel.key == key)
                .values(**changes, updated_at=func.now())
                .returning(ConfigModel)
                .execution_options(populate_existing=True)
            )
            entity = (await self.session.scalars(statement)).one()
        return entity
