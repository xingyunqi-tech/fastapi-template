"""注销账号后删除该用户的全部收藏（nestjs favorites-account.cleaner.ts）。"""

from typing import Final

from app.infrastructure.account_cleanup import AccountDataCleaner, register_account_cleaner
from app.modules.favorites.repository import FavoriteRepository
lazy from app.common.context import AppContext


class FavoritesAccountCleaner:
    name: Final = "favorites"

    def __init__(self, context: AppContext) -> None:
        self.context = context

    async def clean(self, user_id: int) -> None:
        async with self.context.database.transaction() as session:
            await FavoriteRepository(session).remove_by_user(user_id)


@register_account_cleaner(70)
def favorites_account_cleaner(context: AppContext) -> AccountDataCleaner:
    return FavoritesAccountCleaner(context)
