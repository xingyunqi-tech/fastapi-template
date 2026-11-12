"""行政区划查询（nestjs RegionsService）：列表、树与按编码查询缓存 7 天，不存在的编码缓存 60 秒。"""

import logging
from typing import Any, Final

lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import iso_timestamp
from app.modules.regions.repository import RegionRepository
lazy from app.infrastructure.cache import Cache
lazy from app.modules.regions.models import RegionModel

logger = logging.getLogger("app.regions")

CACHE_KEY_PREFIX: Final = "region:"
CACHE_TTL_MS: Final = 7 * 24 * 60 * 60 * 1000
MISS_CACHE_TTL_MS: Final = 60 * 1000

type RegionView = dict[str, Any]


def serialize_region(region: RegionModel) -> RegionView:
    return {
        "id": region.id,
        "code": region.code,
        "name": region.name,
        "level": region.level,
        "parentCode": region.parent_code,
        "sort": region.sort,
        "createdAt": iso_timestamp(region.created_at),
        "updatedAt": iso_timestamp(region.updated_at),
    }


def _serialize(regions: list[RegionModel]) -> list[RegionView]:
    return [serialize_region(region) for region in regions]


class RegionsService:
    def __init__(self, session: AsyncSession, cache: Cache) -> None:
        self.repository = RegionRepository(session)
        self.cache = cache

    async def _cached_list(self, key: str) -> list[RegionView] | None:
        cached = await self.cache.get(f"{CACHE_KEY_PREFIX}{key}")
        return cached if isinstance(cached, list) and cached else None

    async def _store_list(self, key: str, regions: list[RegionView]) -> None:
        if regions:
            await self.cache.set(f"{CACHE_KEY_PREFIX}{key}", regions, CACHE_TTL_MS)

    async def get_provinces(self) -> list[RegionView]:
        cached = await self._cached_list("provinces")
        if cached is not None:
            return cached
        logger.info("Fetching all provinces from database")
        provinces = _serialize(await self.repository.find_by_level(1))
        await self._store_list("provinces", provinces)
        return provinces

    async def get_cities(self, province_code: str) -> list[RegionView]:
        key = f"cities:{province_code}"
        cached = await self._cached_list(key)
        if cached is not None:
            return cached
        logger.info("Fetching cities for province: %s from database", province_code)
        cities = _serialize(await self.repository.find_by_parent_code(province_code))
        await self._store_list(key, cities)
        return cities

    async def get_districts(self, city_code: str) -> list[RegionView]:
        key = f"districts:{city_code}"
        cached = await self._cached_list(key)
        if cached is not None:
            return cached
        logger.info("Fetching districts for city: %s from database", city_code)
        districts = _serialize(await self.repository.find_by_parent_code(city_code))
        await self._store_list(key, districts)
        return districts

    async def query(
        self, *, level: int | None, parent_code: str | None, keyword: str | None
    ) -> list[RegionView]:
        if keyword:
            logger.info("Searching regions by keyword: %s", keyword)
            return _serialize(await self.repository.search_by_name(keyword))
        if parent_code is not None:
            logger.info("Fetching regions by parent code: %s", parent_code)
            return _serialize(await self.repository.find_by_parent_code(parent_code))
        if level:
            logger.info("Fetching regions by level: %s", level)
            return _serialize(await self.repository.find_by_level(level))
        logger.info("Fetching all regions")
        return _serialize(await self.repository.find_all())

    async def get_tree(self) -> list[RegionView]:
        cached = await self._cached_list("tree")
        if cached is not None:
            return cached
        logger.info("Building region tree from database")
        regions = await self.repository.find_all()
        nodes = {region.code: {**serialize_region(region), "children": []} for region in regions}
        tree: list[RegionView] = []
        for region in regions:
            node = nodes[region.code]
            if region.parent_code is None:
                tree.append(node)
            elif (parent := nodes.get(region.parent_code)) is not None:
                parent["children"].append(node)
        await self._store_list("tree", tree)
        return tree

    async def get_by_code(self, code: str) -> RegionView | None:
        cache_key = f"{CACHE_KEY_PREFIX}code:{code}"
        miss_key = f"{CACHE_KEY_PREFIX}code-miss:{code}"
        cached = await self.cache.get(cache_key)
        if cached:
            return dict(cached)
        if await self.cache.get(miss_key):
            return None
        logger.info("Fetching region by code: %s from database", code)
        region = await self.repository.find_by_code(code)
        if region is None:
            await self.cache.set(miss_key, True, MISS_CACHE_TTL_MS)
            return None
        view = serialize_region(region)
        await self.cache.set(cache_key, view, CACHE_TTL_MS)
        return view

    async def validate_region_combination(
        self, province_code: str, city_code: str, district_code: str
    ) -> tuple[RegionView, RegionView, RegionView] | None:
        """省市区编码须逐级匹配（level 1/2/3 且 parentCode 对应），否则返回 None。"""
        province, city, district = [
            await self.get_by_code(code) for code in (province_code, city_code, district_code)
        ]
        if province is None or city is None or district is None:
            return None
        if (province["level"], city["level"], district["level"]) != (1, 2, 3):
            return None
        if city["parentCode"] != province_code or district["parentCode"] != city_code:
            return None
        return province, city, district
