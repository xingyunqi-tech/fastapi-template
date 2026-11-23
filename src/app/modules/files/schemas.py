from typing import TYPE_CHECKING
from uuid import UUID

from app.common.types import ApiModel, UtcDatetime
from app.modules.files.purposes import Purpose

if TYPE_CHECKING:
    from app.modules.files.models import FileModel
    from app.modules.files.urls import FileUrls


class File(ApiModel):
    """约定 §11.4。"""

    id: UUID
    name: str
    size: int
    content_type: str
    purpose: Purpose
    url: str
    created_at: UtcDatetime

    @classmethod
    def from_model(cls, file: FileModel, urls: FileUrls) -> File:
        return cls(
            id=file.id,
            name=file.name,
            size=file.size,
            content_type=file.content_type,
            purpose=Purpose(file.purpose),
            url=urls.build(file),
            created_at=file.created_at,
        )
