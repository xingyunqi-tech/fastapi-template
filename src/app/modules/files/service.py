"""上传、读取与按所有者清理；类型按文件内容识别，大小按用途上限判定。"""

from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from app.common.errors import AppError, field_error
from app.infrastructure.database.session import after_rollback
from app.modules.files.models import FileModel
from app.modules.files.purposes import RULES, SNIFF_BYTES, Purpose, sniff
from app.modules.files.storage import build_storage, new_key

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import IO
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.config import StorageSettings

NAME_MAX_LENGTH: Final = 255


def _size(body: IO[bytes]) -> int:
    body.seek(0, 2)
    size = body.tell()
    body.seek(0)
    return size


class FilesService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_by_id(self, file_id: UUID) -> FileModel | None:
        return await self.session.get(FileModel, file_id)

    async def find_owned(self, file_id: UUID, owner_id: str) -> FileModel:
        """不存在或不是本人上传时 404 `common.notFound`。"""
        file = await self.find_by_id(file_id)
        if file is None or file.owner_id != owner_id:
            raise AppError("common.notFound")
        return file

    async def find_by_owner(
        self, owner_id: str, after_id: UUID | None, limit: int
    ) -> Sequence[FileModel]:
        query = select(FileModel).where(FileModel.owner_id == owner_id)
        if after_id is not None:
            query = query.where(FileModel.id > after_id)
        return list(await self.session.scalars(query.order_by(FileModel.id).limit(limit)))

    async def upload(
        self,
        storage_settings: StorageSettings,
        *,
        owner_id: str,
        purpose_value: str | None,
        name: str,
        body: IO[bytes],
    ) -> FileModel:
        """用途缺失或非法 422；超出用途上限 413 `file.tooLarge`；类型不符 422。"""
        if not purpose_value:
            raise field_error("purpose", "required")
        try:
            purpose = Purpose(purpose_value)
        except ValueError:
            raise field_error("purpose", "enum", values=[p.value for p in Purpose]) from None
        rule = RULES[purpose]
        size = _size(body)
        if size > rule.max_size:
            raise AppError("file.tooLarge")
        content_type = sniff(body.read(SNIFF_BYTES))
        body.seek(0)
        if content_type is None or content_type not in rule.content_types:
            raise AppError("file.typeNotAllowed")
        key = new_key()
        storage = build_storage(storage_settings)
        await storage.put(key, body, content_type)
        after_rollback(self.session, lambda: storage.delete(key))
        file = FileModel(
            owner_id=owner_id,
            purpose=purpose.value,
            storage_key=key,
            name=name[:NAME_MAX_LENGTH] or key,
            content_type=content_type,
            size=size,
        )
        self.session.add(file)
        await self.session.flush()
        await self.session.refresh(file)
        return file
