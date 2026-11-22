"""文件上传请求与响应模型。"""

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import Field

from app.common.types import ApiModel

if TYPE_CHECKING:
    from app.modules.files.models import FileModel
    from app.modules.files.urls import FileUrls


class FileInfo(ApiModel):
    id: UUID
    path: str
    visibility: str
    mime_type: str | None
    size: int | None

    @classmethod
    def from_model(cls, file: FileModel, urls: FileUrls) -> FileInfo:
        return cls(
            id=file.id,
            path=urls.build(file),
            visibility=file.visibility or "PUBLIC",
            mime_type=file.mime_type,
            size=file.size,
        )


class UploadedFile(ApiModel):
    file: FileInfo


class PresignedUploadRequest(ApiModel):
    file_name: str = Field(min_length=1)
    file_size: int = Field(ge=0)


class PresignedUpload(ApiModel):
    file: FileInfo
    upload_signed_url: str
