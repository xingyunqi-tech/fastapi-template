"""查询参数 DTO 与 toggleFavorite 的响应（基线中为内联 schema）。"""

from pydantic import Field

from app.contract.schemas import ContractModel, Favorite


class QueryFavoriteDto(ContractModel):
    target_type: str | None = Field(default=None, alias="targetType")
    subject_id: str | None = Field(default=None, alias="subjectId")
    page: int | float | None = Field(default=None, alias="page")
    limit: int | float | None = Field(default=None, alias="limit")


class ToggleFavoriteResponse(ContractModel):
    is_favorited: bool | None = Field(default=None, alias="isFavorited")
    favorite: Favorite | None = Field(default=None, alias="favorite")
