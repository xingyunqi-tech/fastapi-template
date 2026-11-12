lazy from collections.abc import Sequence
lazy from uuid import UUID

from sqlalchemy import select
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import forbidden, not_found
from app.modules.files.models import FileModel
from app.modules.files.purposes import FileVisibility

PRIVATE = FileVisibility.PRIVATE.value


def can_access(file: FileModel, user_id: int | None) -> bool:
    if file.visibility != PRIVATE:
        return True
    return user_id is not None and file.owner_user_id == user_id


class FilesService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_by_id(self, file_id: UUID) -> FileModel | None:
        return await self.session.get(FileModel, file_id)

    async def find_by_ids(self, ids: Sequence[UUID]) -> list[FileModel]:
        if not ids:
            return []
        return list(await self.session.scalars(select(FileModel).where(FileModel.id.in_(ids))))

    async def find_by_path(self, path: str) -> FileModel | None:
        return await self.session.scalar(select(FileModel).where(FileModel.path == path).limit(1))

    async def find_by_owner(
        self, owner_user_id: int, after_id: UUID | None, limit: int
    ) -> list[FileModel]:
        """该用户上传的文件，按 id 升序取 after_id 之后的 limit 条。"""
        query = select(FileModel).where(FileModel.owner_user_id == owner_user_id)
        if after_id is not None:
            query = query.where(FileModel.id > after_id)
        return list(await self.session.scalars(query.order_by(FileModel.id).limit(limit)))

    async def create(
        self,
        *,
        path: str,
        owner_user_id: int | None,
        visibility: FileVisibility,
        mime_type: str | None,
        size: int | None,
    ) -> FileModel:
        file = FileModel(
            path=path,
            owner_user_id=owner_user_id,
            visibility=visibility.value,
            mime_type=mime_type,
            size=size,
        )
        self.session.add(file)
        await self.session.flush()
        return file

    async def find_accessible(self, file_id: UUID, user_id: int | None) -> FileModel:
        """不存在时 404 `fileNotFound`，无权访问时 403 `fileNotAccessible`。"""
        file = await self.find_by_id(file_id)
        if file is None:
            raise not_found({"error": "fileNotFound"})
        if not can_access(file, user_id):
            raise forbidden({"error": "fileNotAccessible"})
        return file

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
