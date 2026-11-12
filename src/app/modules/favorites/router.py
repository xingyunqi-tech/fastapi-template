from typing import Annotated, Any

from pydantic import BaseModel
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import pagination
from app.common.params import Body, IdParam, Query
from app.common.routing import ApiRouter, Auth
from app.contract.schemas import (
    CheckFavoriteDto,
    CheckFavoriteResponseDto,
    CreateFavoriteDto,
    Favorite,
    PaginationFavoriteItemDtoResponseDto,
)
from app.core.auth.deps import CurrentUser
from app.infrastructure.database.session import DbSession
from app.modules.favorites.repository import FavoriteRepository
from app.modules.favorites.schemas import QueryFavoriteDto, ToggleFavoriteResponse
from app.modules.favorites.serializers import serialize_favorite, serialize_favorite_item
from app.modules.favorites.service import MAX_PAGE_SIZE, FavoritesService

router = ApiRouter(prefix="/v1/favorites", tags=["Favorites"])


def build_favorites_service(session: AsyncSession) -> FavoritesService:
    return FavoritesService(FavoriteRepository(session))


def _provided(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(by_alias=True, exclude_unset=True)


@router.endpoint(
    "POST",
    "",
    operation_id="createFavorite",
    auth=Auth.JWT,
    response_model=Favorite,
    summary="添加收藏；题目、试卷由服务端校验并写入标题快照与科目，忽略传入的 title、image",
)
async def create_favorite(
    body: Annotated[CreateFavoriteDto, Body(CreateFavoriteDto)],
    user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    favorite = await build_favorites_service(session).create(user.id, _provided(body))
    return serialize_favorite(favorite)


@router.endpoint(
    "GET",
    "",
    operation_id="getFavorites",
    auth=Auth.JWT,
    response_model=PaginationFavoriteItemDtoResponseDto,
    summary="获取收藏列表，可按类型与科目筛选；题目、试卷附带下架状态",
)
async def get_favorites(
    query: Annotated[QueryFavoriteDto, Query(QueryFavoriteDto)],
    user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    result = await build_favorites_service(session).find_page(
        user.id,
        target_type=query.target_type,
        subject_id=query.subject_id,
        page=query.page if query.page is not None else 1,
        limit=min(query.limit if query.limit is not None else 10, MAX_PAGE_SIZE),
    )
    items = [serialize_favorite_item(favorite, offline) for favorite, offline in result.items]
    return pagination(items, result.total, result.page, result.limit)


@router.endpoint(
    "POST",
    "/check",
    operation_id="checkFavorite",
    auth=Auth.JWT,
    response_model=CheckFavoriteResponseDto,
    summary="检查是否已收藏",
)
async def check_favorite(
    body: Annotated[CheckFavoriteDto, Body(CheckFavoriteDto)],
    user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    return await build_favorites_service(session).check(user.id, body.target_type, body.target_id)


@router.endpoint(
    "POST",
    "/toggle",
    operation_id="toggleFavorite",
    auth=Auth.JWT,
    response_model=ToggleFavoriteResponse,
    summary="切换收藏状态",
)
async def toggle_favorite(
    body: Annotated[CreateFavoriteDto, Body(CreateFavoriteDto)],
    user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    favorited, favorite = await build_favorites_service(session).toggle(user.id, _provided(body))
    if favorite is None:
        return {"isFavorited": favorited}
    return {"isFavorited": favorited, "favorite": serialize_favorite(favorite)}


@router.endpoint(
    "DELETE",
    "/{id}",
    operation_id="deleteFavorite",
    auth=Auth.JWT,
    summary="取消收藏",
)
async def delete_favorite(id: IdParam, user: CurrentUser, session: DbSession) -> None:
    await build_favorites_service(session).remove(id, user.id)
