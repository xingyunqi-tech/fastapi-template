"""查询参数与路径参数 DTO（nestjs QueryRegionDto、ProvinceCodeParam 等，不在 components 中）。"""

from typing import Annotated

from fastapi import Depends, Path
from pydantic import Field

from app.common.params import get_validator
from app.common.validation.engine import run_pipe
from app.contract.schemas import ContractModel


class QueryRegionDto(ContractModel):
    level: int | float | None = Field(default=None, alias="level")
    parent_code: str | None = Field(default=None, alias="parentCode")
    keyword: str | None = Field(default=None, alias="keyword")


def _validate_param(dto: str, name: str, value: str) -> str:
    run_pipe(get_validator(), dto, {name: value}, "param")
    return value


async def _province_code(provinceCode: Annotated[str, Path()]) -> str:  # noqa: N803
    return _validate_param("ProvinceCodeParam", "provinceCode", provinceCode)


async def _city_code(cityCode: Annotated[str, Path()]) -> str:  # noqa: N803
    return _validate_param("CityCodeParam", "cityCode", cityCode)


async def _region_code(code: Annotated[str, Path()]) -> str:
    return _validate_param("RegionCodeParam", "code", code)


ProvinceCodeParam = Annotated[str, Depends(_province_code)]
CityCodeParam = Annotated[str, Depends(_city_code)]
RegionCodeParam = Annotated[str, Depends(_region_code)]
