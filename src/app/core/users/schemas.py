"""查询参数 DTO（基线中以 JSON 字符串查询参数出现，不在 components 中）。"""

from pydantic import Field

from app.contract.schemas import ContractModel, RoleDto


class FilterUserDto(ContractModel):
    roles: list[RoleDto] | None = Field(default=None, alias="roles")


class SortUserDto(ContractModel):
    order_by: str = Field(alias="orderBy")
    order: str = Field(alias="order")


class QueryUserDto(ContractModel):
    page: int | float | None = Field(default=None, alias="page")
    limit: int | float | None = Field(default=None, alias="limit")
    filters: FilterUserDto | None = Field(default=None, alias="filters")
    sort: list[SortUserDto] | None = Field(default=None, alias="sort")
