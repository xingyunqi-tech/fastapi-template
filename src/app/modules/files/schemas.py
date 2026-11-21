"""文件上传请求与响应模型。"""

from pydantic import Field

from app.common.types import ApiModel
from app.core.users.schemas import FileInfo


class UploadedFile(ApiModel):
    file: FileInfo


class PresignedUploadRequest(ApiModel):
    file_name: str = Field(min_length=1)
    file_size: int = Field(ge=0)


class PresignedUpload(ApiModel):
    file: FileInfo
    upload_signed_url: str
