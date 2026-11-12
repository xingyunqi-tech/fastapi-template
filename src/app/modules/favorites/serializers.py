"""与 nestjs Favorite / FavoriteItemDto 输出一致的序列化（键顺序同 FavoriteMapper）。"""

from typing import Any

from app.common.responses import iso_timestamp
lazy from app.modules.favorites.models import FavoriteModel


def serialize_favorite(favorite: FavoriteModel) -> dict[str, Any]:
    return {
        "id": favorite.id,
        "userId": favorite.user_id,
        "targetType": favorite.target_type,
        "targetId": favorite.target_id,
        "title": favorite.title,
        "image": favorite.image,
        "extra": favorite.extra,
        "subjectId": str(favorite.subject_id) if favorite.subject_id is not None else None,
        "createdAt": iso_timestamp(favorite.created_at),
    }


def serialize_favorite_item(favorite: FavoriteModel, target_offline: bool | None) -> dict[str, Any]:
    return {**serialize_favorite(favorite), "targetOffline": target_offline}
