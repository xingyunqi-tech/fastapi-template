"""注销账号后删除该用户上传的文件（nestjs files-account.cleaner.ts）。"""

import logging
from typing import TYPE_CHECKING, Final

from sqlalchemy import delete
from sqlalchemy.exc import DBAPIError

from app.infrastructure.account_cleanup import AccountDataCleaner, register_account_cleaner
from app.modules.files.models import FileModel
from app.modules.files.service import FilesService
from app.modules.files.storage import FileStorage

if TYPE_CHECKING:
    from uuid import UUID

    from app.common.context import AppContext
    from app.config import FileSettings
    from app.infrastructure.database.session import Database

logger = logging.getLogger("app.files")

BATCH_SIZE: Final = 100
PG_FOREIGN_KEY_VIOLATION: Final = "23503"


def is_foreign_key_violation(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if code == PG_FOREIGN_KEY_VIOLATION:
            return True
        orig = getattr(current, "orig", None)
        current = orig if isinstance(orig, BaseException) else current.__cause__
    return False


class FilesAccountCleaner:
    """删除记录与存储对象；仍被外键引用的文件保留。清理顺序 1000。"""

    name: Final = "files"
    order: Final = 1000

    def __init__(self, database: Database, settings: FileSettings) -> None:
        self.database = database
        self.storage = FileStorage(settings)

    async def clean(self, user_id: int) -> None:
        after_id: UUID | None = None
        while True:
            async with self.database.transaction() as session:
                batch = await FilesService(session).find_by_owner(user_id, after_id, BATCH_SIZE)
            for file in batch:
                await self._remove(file)
            if len(batch) < BATCH_SIZE:
                return
            after_id = batch[-1].id

    async def _remove(self, file: FileModel) -> None:
        """每个文件一个事务：先删记录再删存储对象，存储删除失败时回滚该记录并抛出。"""
        try:
            async with self.database.transaction() as session:
                await session.execute(delete(FileModel).where(FileModel.id == file.id))
                await self.storage.delete(file.path)
        except DBAPIError as error:
            if not is_foreign_key_violation(error):
                raise
            logger.warning("File %s is still referenced and kept", file.id)


@register_account_cleaner(FilesAccountCleaner.order)
def files_account_cleaner(context: AppContext) -> AccountDataCleaner:
    return FilesAccountCleaner(context.database, context.settings.file)
