"""查询参数 DTO（基线中以 JSON 字符串查询参数出现，不在 components 中）。"""

from pydantic import Field

from app.contract.schemas import ContractModel


class FilterAddressDto(ContractModel):
    province: str | None = Field(default=None, alias="province")
    city: str | None = Field(default=None, alias="city")
    province_code: str | None = Field(default=None, alias="provinceCode")
    city_code: str | None = Field(default=None, alias="cityCode")
    district_code: str | None = Field(default=None, alias="districtCode")


class SortAddressDto(ContractModel):
    order_by: str = Field(alias="orderBy")
    order: str = Field(alias="order")


class QueryAddressDto(ContractModel):
    page: int | float | None = Field(default=None, alias="page")
    limit: int | float | None = Field(default=None, alias="limit")
    filters: FilterAddressDto | None = Field(default=None, alias="filters")
    sort: list[SortAddressDto] | None = Field(default=None, alias="sort")
