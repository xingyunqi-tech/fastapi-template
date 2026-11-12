"""查询参数 DTO。"""

from pydantic import Field

from app.contract.schemas import ContractModel


class QueryFeedbackDto(ContractModel):
    type_: str | None = Field(default=None, alias="type")
    status: str | None = Field(default=None, alias="status")
    page: int | float | None = Field(default=None, alias="page")
    limit: int | float | None = Field(default=None, alias="limit")
