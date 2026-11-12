"""注销账号后物理删除该用户的全部通知与通知设置（nestjs notifications-account.cleaner.ts）。"""

from typing import Final

from app.infrastructure.account_cleanup import AccountDataCleaner, register_account_cleaner
from app.modules.notifications.repository import (
    NotificationRepository,
    NotificationSettingRepository,
)
lazy from app.common.context import AppContext


class NotificationsAccountCleaner:
    name: Final = "notifications"

    def __init__(self, context: AppContext) -> None:
        self.context = context

    async def clean(self, user_id: int) -> None:
        async with self.context.database.transaction() as session:
            await NotificationRepository(session).remove_by_user(user_id)
            await NotificationSettingRepository(session).remove_by_user(user_id)


@register_account_cleaner(100)
def notifications_account_cleaner(context: AppContext) -> AccountDataCleaner:
    return NotificationsAccountCleaner(context)
