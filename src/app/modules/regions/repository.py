import re
from typing import Final

from sqlalchemy import select
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.regions.models import RegionModel

SEARCH_LIMIT: Final = 20
_LIKE_SPECIAL: Final = re.compile(r"[%_]")


class RegionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_by_code(self, code: str) -> RegionModel | None:
        return await self.session.scalar(select(RegionModel).where(RegionModel.code == code))

    async def find_by_level(self, level: int) -> list[RegionModel]:
        return list(
            await self.session.scalars(
                select(RegionModel)
                .where(RegionModel.level == level)
                .order_by(RegionModel.sort.asc(), RegionModel.code.asc())
            )
        )

    async def find_by_parent_code(self, parent_code: str | None) -> list[RegionModel]:
        condition = (
            RegionModel.parent_code.is_(None)
            if parent_code is None
            else RegionModel.parent_code == parent_code
        )
        return list(
            await self.session.scalars(
                select(RegionModel)
                .where(condition)
                .order_by(RegionModel.sort.asc(), RegionModel.code.asc())
            )
        )

    async def find_all(self) -> list[RegionModel]:
        return list(
            await self.session.scalars(
                select(RegionModel).order_by(
                    RegionModel.level.asc(), RegionModel.sort.asc(), RegionModel.code.asc()
                )
            )
        )

    async def search_by_name(self, keyword: str, limit: int = SEARCH_LIMIT) -> list[RegionModel]:
        escaped = _LIKE_SPECIAL.sub(lambda match: "\\" + match.group(0), keyword)
        return list(
            await self.session.scalars(
                select(RegionModel)
                .where(RegionModel.name.ilike(f"%{escaped}%"))
                .order_by(RegionModel.level.asc(), RegionModel.sort.asc())
                .limit(limit)
            )
        )
