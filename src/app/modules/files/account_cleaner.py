"""注销后删除该用户上传的文件：先删存储对象再删记录，按 id 分批，可重复执行。"""

from typing import TYPE_CHECKING, Final

from sqlalchemy import delete

from app.infrastructure.account_data import AccountDataCleaner, register_account_cleaner
from app.modules.files.models import FileModel
from app.modules.files.service import FilesService
from app.modules.files.storage import build_storage

if TYPE_CHECKING:
    from uuid import UUID

    from app.common.context import AppContext
    from app.config import StorageSettings
    from app.infrastructure.database.session import Database

BATCH_SIZE: Final = 100
ORDER: Final = 1000


class FilesAccountCleaner:
    name: Final = "files"

    def __init__(self, database: Database, settings: StorageSettings) -> None:
        self.database = database
        self.storage = build_storage(settings)

    async def clean(self, user_id: UUID) -> None:
        owner = str(user_id)
        while True:
            async with self.database.transaction() as session:
                batch = await FilesService(session).find_by_owner(owner, None, BATCH_SIZE)
            for file in batch:
                await self.storage.delete(file.storage_key)
                async with self.database.transaction() as session:
                    await session.execute(delete(FileModel).where(FileModel.id == file.id))
            if len(batch) < BATCH_SIZE:
                return


@register_account_cleaner(ORDER)
def files_account_cleaner(context: AppContext) -> AccountDataCleaner | None:
    if context.database is None or context.settings.storage.driver is None:
        return None
    return FilesAccountCleaner(context.database, context.settings.storage)
