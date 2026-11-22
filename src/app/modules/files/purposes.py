"""上传用途规则。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
lazy from uuid import UUID

from app.common.errors import AppError, ValidationFailed, field_error
from app.security import rbac

FILE_MANAGE: Final = rbac.register("file:manage", roles=())
"""内容素材、导入导出用途的上传权限，只有 `admin` 拥有。"""


class FilePurpose(StrEnum):
    IMAGE = "image"
    PRIVATE_IMAGE = "private-image"
    CONTENT_IMAGE = "content-image"
    AUDIO = "audio"
    IMPORT = "import"
    EXPORT = "export"


class FileVisibility(StrEnum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"


@dataclass(frozen=True, slots=True)
class FilePurposeRule:
    mime_types: dict[str, str]
    visibility: FileVisibility
    permission: str | None = None
    content_asset: bool = False
    """内容素材：文件无归属用户，不随上传者注销删除。"""


_IMAGE_TYPES: Final = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}

FILE_PURPOSE_RULES: Final[dict[FilePurpose, FilePurposeRule]] = {
    FilePurpose.IMAGE: FilePurposeRule(_IMAGE_TYPES, FileVisibility.PUBLIC),
    FilePurpose.PRIVATE_IMAGE: FilePurposeRule(
        {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"},
        FileVisibility.PRIVATE,
    ),
    FilePurpose.CONTENT_IMAGE: FilePurposeRule(
        _IMAGE_TYPES, FileVisibility.PUBLIC, FILE_MANAGE, content_asset=True
    ),
    FilePurpose.AUDIO: FilePurposeRule(
        {"mp3": "audio/mpeg", "m4a": "audio/mp4"},
        FileVisibility.PUBLIC,
        FILE_MANAGE,
        content_asset=True,
    ),
    FilePurpose.IMPORT: FilePurposeRule(
        {
            "zip": "application/zip",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "json": "application/json",
        },
        FileVisibility.PRIVATE,
        FILE_MANAGE,
    ),
    FilePurpose.EXPORT: FilePurposeRule(
        {"pdf": "application/pdf"}, FileVisibility.PRIVATE, FILE_MANAGE
    ),
}


def file_owner_for(purpose: FilePurpose, uploader_id: UUID | None) -> UUID | None:
    return None if FILE_PURPOSE_RULES[purpose].content_asset else uploader_id


def parse_file_purpose(value: Any) -> FilePurpose | None:
    """未传或空串按公开图片处理，无法识别时返回 None。"""
    if value is None or value == "":
        return FilePurpose.IMAGE
    if isinstance(value, str) and value in FilePurpose:
        return FilePurpose(value)
    return None


def file_extension(file_name: str) -> str:
    _, dot, extension = file_name.rpartition(".")
    return extension.lower() if dot else ""


def mime_type_for(purpose: FilePurpose, file_name: str) -> str | None:
    return FILE_PURPOSE_RULES[purpose].mime_types.get(file_extension(file_name))


def invalid_purpose_error() -> ValidationFailed:
    return field_error("query.purpose", "enum", values=[purpose.value for purpose in FilePurpose])


def file_type_not_allowed_error() -> AppError:
    return AppError("file.typeNotAllowed")


def select_file_error() -> ValidationFailed:
    return field_error("file", "required")


def file_too_large_error() -> AppError:
    return AppError("file.tooLarge")
