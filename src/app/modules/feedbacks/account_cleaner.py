"""注销账号后清空反馈的 userId、联系方式与图片（nestjs feedbacks-account.cleaner.ts）。"""

from typing import Final

from app.infrastructure.account_cleanup import AccountDataCleaner, register_account_cleaner
from app.modules.feedbacks.repository import FeedbackRepository
lazy from app.common.context import AppContext


class FeedbacksAccountCleaner:
    name: Final = "feedbacks"

    def __init__(self, context: AppContext) -> None:
        self.context = context

    async def clean(self, user_id: int) -> None:
        async with self.context.database.transaction() as session:
            await FeedbackRepository(session).anonymize_by_user(user_id)


@register_account_cleaner(90)
def feedbacks_account_cleaner(context: AppContext) -> AccountDataCleaner:
    return FeedbacksAccountCleaner(context)
