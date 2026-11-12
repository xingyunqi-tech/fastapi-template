"""查询参数 DTO（基线中展开为查询参数，不在 components 中）。"""

from pydantic import Field

from app.contract.schemas import ContractModel


class QueryNotificationDto(ContractModel):
    page: int | float | None = Field(default=None, alias="page")
    limit: int | float | None = Field(default=None, alias="limit")
    category: str | None = Field(default=None, alias="category")
    is_read: bool | None = Field(default=None, alias="isRead")
