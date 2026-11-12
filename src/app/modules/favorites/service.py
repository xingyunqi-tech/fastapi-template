"""收藏（nestjs FavoritesService + FavoriteTargetsService）；题目、试卷目标按目标不存在处理。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
lazy from collections.abc import Mapping
lazy from uuid import UUID

from app.common.exceptions import BusinessException, HttpError, conflict, not_found_error
from app.common.validation.validator_js import is_uuid
lazy from app.modules.favorites.models import FavoriteModel
lazy from app.modules.favorites.repository import FavoriteRepository

MAX_PAGE_SIZE: Final = 50


class FavoriteTargetType(StrEnum):
    PRODUCT = "product"
    ARTICLE = "article"
    STORE = "store"
    QUESTION = "question"
    PAPER = "paper"


EXAM_TARGET_TYPES: Final = frozenset({FavoriteTargetType.QUESTION, FavoriteTargetType.PAPER})


def normalize_target_id(target_type: str, target_id: str) -> str:
    """题目与试卷的 UUID 转为小写。"""
    if target_type in EXAM_TARGET_TYPES and is_uuid(target_id):
        return target_id.lower()
    return target_id


@dataclass(frozen=True, slots=True)
class FavoritePage:
    items: list[tuple[FavoriteModel, bool | None]]
    total: int
    page: float
    limit: float


class FavoritesService:
    def __init__(self, repository: FavoriteRepository) -> None:
        self.repository = repository

    def _resolve(self, body: Mapping[str, Any]) -> dict[str, Any]:
        target_type = body["targetType"]
        if target_type == FavoriteTargetType.QUESTION:
            raise BusinessException("questionNotAccessible")
        if target_type == FavoriteTargetType.PAPER:
            raise not_found_error("paperNotFound")
        return {
            "target_type": target_type,
            "target_id": body["targetId"],
            "title": body.get("title"),
            "image": body.get("image"),
            "extra": body.get("extra"),
            "subject_id": None,
        }

    async def find_by_target(
        self, user_id: int, target_type: str, target_id: str
    ) -> FavoriteModel | None:
        return await self.repository.find_by_user_and_target(
            user_id, target_type, normalize_target_id(target_type, target_id)
        )

    async def create(self, user_id: int, body: Mapping[str, Any]) -> FavoriteModel:
        values = self._resolve(body)
        if await self.find_by_target(user_id, values["target_type"], values["target_id"]):
            raise conflict({"error": "alreadyFavorited"})
        values["target_id"] = normalize_target_id(values["target_type"], values["target_id"])
        return await self.repository.create({**values, "user_id": user_id})

    async def toggle(
        self, user_id: int, body: Mapping[str, Any]
    ) -> tuple[bool, FavoriteModel | None]:
        existing = await self.find_by_target(user_id, body["targetType"], body["targetId"])
        if existing is not None:
            await self.remove(existing.id, user_id)
            return False, None
        return True, await self.create(user_id, body)

    async def check(self, user_id: int, target_type: str, target_id: str) -> dict[str, Any]:
        favorite = await self.find_by_target(user_id, target_type, target_id)
        result: dict[str, Any] = {"isFavorited": favorite is not None}
        if favorite is not None:
            result["favoriteId"] = favorite.id
        return result

    async def remove(self, favorite_id: int, user_id: int) -> None:
        favorite = await self.repository.find_by_id(favorite_id)
        if favorite is None or favorite.user_id != user_id:
            raise _favorite_not_found()
        await self.repository.remove(favorite_id)

    async def find_page(
        self,
        user_id: int,
        *,
        target_type: str | None,
        subject_id: str | None,
        page: float,
        limit: float,
    ) -> FavoritePage:
        subject = UUID(subject_id) if subject_id else None
        data = await self.repository.find_many(
            user_id, target_type=target_type, subject_id=subject, page=page, limit=limit
        )
        total = await self.repository.count(user_id, target_type=target_type, subject_id=subject)
        items = [
            (favorite, True if favorite.target_type in EXAM_TARGET_TYPES else None)
            for favorite in data
        ]
        return FavoritePage(items, total, page, limit)


def _favorite_not_found() -> HttpError:
    return not_found_error("favoriteNotFound")
