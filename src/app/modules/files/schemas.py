"""文件上传 DTO（类名与 nestjs 一致，FileType 来自基线生成的契约模型）。"""

from pydantic import ConfigDict, Field

from app.contract.schemas import ContractModel, FileType


class UploadFileQueryDto(ContractModel):
    purpose: str | None = Field(default=None, alias="purpose")


class FileResponseDto(ContractModel):
    file: FileType = Field(alias="file")


class FileUploadDto(ContractModel):
    file_name: str = Field(alias="fileName")
    file_size: int | float = Field(alias="fileSize")


class PresignedFileResponseDto(ContractModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore", title="FileResponseDto")

    file: FileType = Field(alias="file")
    upload_signed_url: str = Field(alias="uploadSignedUrl")
