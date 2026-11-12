lazy from collections.abc import Sequence
lazy from uuid import UUID

from sqlalchemy import select
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import forbidden, not_found
from app.modules.files.models import FileModel

PRIVATE = "PRIVATE"


def can_access(file: FileModel, user_id: int | None) -> bool:
    if file.visibility != PRIVATE:
        return True
    return user_id is not None and file.owner_user_id == user_id


class FilesService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_by_ids(self, ids: Sequence[UUID]) -> list[FileModel]:
        if not ids:
            return []
        return list(await self.session.scalars(select(FileModel).where(FileModel.id.in_(ids))))

    async def assert_referenceable(
        self, ids: Sequence[UUID], user_id: int | None
    ) -> list[FileModel]:
        """不存在时 404 `fileNotFound`，无权访问时 403 `fileNotAccessible`。"""
        unique = list(dict.fromkeys(ids))
        files = await self.find_by_ids(unique)
        if len(files) != len(unique):
            raise not_found({"error": "fileNotFound"})
        if any(not can_access(file, user_id) for file in files):
            raise forbidden({"error": "fileNotAccessible"})
        return files
