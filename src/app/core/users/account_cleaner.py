"""注销账号后解除头像关联，头像文件由文件清理器删除（nestjs users-account.cleaner.ts）。"""

from typing import Final

from app.core.users.repository import UserRepository
from app.infrastructure.account_cleanup import AccountDataCleaner, register_account_cleaner
lazy from app.common.context import AppContext


class UsersAccountCleaner:
    name: Final = "users"

    def __init__(self, context: AppContext) -> None:
        self.context = context

    async def clean(self, user_id: int) -> None:
        async with self.context.database.transaction() as session:
            await UserRepository(session).detach_photo(user_id)


@register_account_cleaner(900)
def users_account_cleaner(context: AppContext) -> AccountDataCleaner:
    return UsersAccountCleaner(context)
