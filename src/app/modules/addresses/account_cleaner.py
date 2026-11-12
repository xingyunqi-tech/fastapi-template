"""注销账号后删除该用户的全部地址（nestjs addresses-account.cleaner.ts）。"""

from typing import Final

from app.infrastructure.account_cleanup import AccountDataCleaner, register_account_cleaner
from app.modules.addresses.repository import AddressRepository
lazy from app.common.context import AppContext


class AddressesAccountCleaner:
    name: Final = "addresses"

    def __init__(self, context: AppContext) -> None:
        self.context = context

    async def clean(self, user_id: int) -> None:
        async with self.context.database.transaction() as session:
            await AddressRepository(session).remove_by_user(user_id)


@register_account_cleaner(110)
def addresses_account_cleaner(context: AppContext) -> AccountDataCleaner:
    return AddressesAccountCleaner(context)
