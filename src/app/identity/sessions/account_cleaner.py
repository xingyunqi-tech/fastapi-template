"""注销账号后删除该用户的会话（`refresh_tokens` 随外键级联删除）。"""

from typing import TYPE_CHECKING, Final
lazy from uuid import UUID

from sqlalchemy import delete

from app.identity.sessions.models import SessionModel
from app.infrastructure.account_data import AccountDataCleaner, register_account_cleaner

if TYPE_CHECKING:
    from app.common.context import AppContext
    from app.infrastructure.database.session import Database


class SessionsAccountCleaner:
    name: Final = "sessions"
    order: Final = 100

    def __init__(self, database: Database) -> None:
        self.database = database

    async def clean(self, user_id: UUID) -> None:
        async with self.database.transaction() as session:
            await session.execute(delete(SessionModel).where(SessionModel.user_id == user_id))


@register_account_cleaner(SessionsAccountCleaner.order)
def sessions_account_cleaner(context: AppContext) -> AccountDataCleaner | None:
    if not context.settings.auth.is_issuer:
        return None
    return SessionsAccountCleaner(context.db)
