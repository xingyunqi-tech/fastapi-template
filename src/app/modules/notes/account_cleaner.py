"""注销账号后删除该用户的笔记。"""

from typing import TYPE_CHECKING, Final

from app.infrastructure.account_data import AccountDataCleaner, register_account_cleaner
from app.modules.notes.repository import NoteRepository

if TYPE_CHECKING:
    from uuid import UUID

    from app.common.context import AppContext
    from app.infrastructure.database.session import Database

ORDER: Final = 1000


class NotesAccountCleaner:
    name: Final = "notes"

    def __init__(self, database: Database) -> None:
        self.database = database

    async def clean(self, user_id: UUID) -> None:
        async with self.database.transaction() as session:
            await NoteRepository(session).delete_by_owner(str(user_id))


@register_account_cleaner(ORDER)
def notes_account_cleaner(context: AppContext) -> AccountDataCleaner | None:
    if context.database is None:
        return None
    return NotesAccountCleaner(context.database)
