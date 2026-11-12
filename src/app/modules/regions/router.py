from typing import Annotated, Any

from app.common.context import Context
from app.common.exceptions import not_found
from app.common.params import Query
from app.common.routing import ApiRouter, Auth
from app.contract.schemas import Region, RegionTreeDto
from app.infrastructure.database.session import DbSession
from app.modules.regions.schemas import (
    CityCodeParam,
    ProvinceCodeParam,
    QueryRegionDto,
    RegionCodeParam,
)
from app.modules.regions.service import RegionsService

router = ApiRouter(prefix="/v1/regions", tags=["Regions"])


@router.endpoint(
    "GET",
    "/provinces",
    operation_id="getProvinces",
    auth=Auth.PUBLIC,
    response_model=list[Region],
    summary="获取所有省份",
)
async def get_provinces(context: Context, session: DbSession) -> list[dict[str, Any]]:
    return await RegionsService(session, context.cache).get_provinces()


@router.endpoint(
    "GET",
    "/cities/{provinceCode}",
    operation_id="getCities",
    auth=Auth.PUBLIC,
    response_model=list[Region],
    summary="获取指定省份下的城市",
)
async def get_cities(
    province_code: ProvinceCodeParam, context: Context, session: DbSession
) -> list[dict[str, Any]]:
    return await RegionsService(session, context.cache).get_cities(province_code)


@router.endpoint(
    "GET",
    "/districts/{cityCode}",
    operation_id="getDistricts",
    auth=Auth.PUBLIC,
    response_model=list[Region],
    summary="获取指定城市下的区县",
)
async def get_districts(
    city_code: CityCodeParam, context: Context, session: DbSession
) -> list[dict[str, Any]]:
    return await RegionsService(session, context.cache).get_districts(city_code)


@router.endpoint(
    "GET",
    "/code/{code}",
    operation_id="getRegionByCode",
    auth=Auth.PUBLIC,
    response_model=Region,
    summary="根据 code 获取地区详情",
)
async def get_region_by_code(
    code: RegionCodeParam, context: Context, session: DbSession
) -> dict[str, Any]:
    region = await RegionsService(session, context.cache).get_by_code(code)
    if region is None:
        raise not_found(
            {"error": "regionNotFound", "message": f"Region with code {code} not found"}
        )
    return region


@router.endpoint(
    "GET",
    "/tree",
    operation_id="getRegionTree",
    auth=Auth.PUBLIC,
    response_model=list[RegionTreeDto],
    summary="获取完整的省市区树形结构",
)
async def get_region_tree(context: Context, session: DbSession) -> list[dict[str, Any]]:
    return await RegionsService(session, context.cache).get_tree()


@router.endpoint(
    "GET",
    "",
    operation_id="queryRegions",
    auth=Auth.PUBLIC,
    response_model=list[Region],
    summary="查询地区（支持按级别、父级代码、关键词搜索）",
)
async def query_regions(
    query: Annotated[QueryRegionDto, Query(QueryRegionDto)], context: Context, session: DbSession
) -> list[dict[str, Any]]:
    return await RegionsService(session, context.cache).query(
        level=int(query.level) if query.level is not None else None,
        parent_code=query.parent_code,
        keyword=query.keyword,
    )
