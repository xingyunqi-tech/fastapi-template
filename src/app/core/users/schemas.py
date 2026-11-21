"""用户请求与响应模型。"""

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Final
from uuid import UUID

from pydantic import BeforeValidator, Field, StringConstraints

from app.common.pagination import Page
from app.common.types import ApiModel, Date, Email, UtcDatetime

if TYPE_CHECKING:
    from app.core.users.models import UserModel
    from app.modules.files.models import FileModel
    from app.modules.files.urls import FileUrls

_TAG_RE: Final = re.compile(r"<[^>]*>")


def _sanitize(value: Any) -> Any:
    return _TAG_RE.sub("", value).strip() if isinstance(value, str) else value


def _lower(value: Any) -> Any:
    return value.lower() if isinstance(value, str) else value


Sanitized = Annotated[str, BeforeValidator(_sanitize)]
NotEmpty = Annotated[str, StringConstraints(min_length=1)]
Nickname = Annotated[str, BeforeValidator(_sanitize), StringConstraints(max_length=50)]
Md5Password = Annotated[str, BeforeValidator(_lower), StringConstraints(pattern=r"^[0-9a-f]{32}$")]
"""客户端提交的密码 MD5（32 位十六进制，转小写）。"""
CnMobile = Annotated[str, StringConstraints(pattern=r"^1[3-9][0-9]{9}$")]
Gender = Annotated[int, Field(ge=0, le=2)]


class Provider(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    WECHAT = "wechat"
    ALIPAY = "alipay"


class FileRef(ApiModel):
    id: UUID


class RoleRef(ApiModel):
    id: int


class StatusRef(ApiModel):
    id: int


class UserFields(ApiModel):
    email: Email | None = None
    password: Md5Password | None = None
    provider: Provider | None = None
    first_name: Sanitized | None = None
    last_name: Sanitized | None = None
    phone: CnMobile | None = None
    nickname: Nickname | None = None
    gender: Gender | None = None
    birthday: Date | None = None
    photo: FileRef | None = None
    role: RoleRef | None = None
    status: StatusRef | None = None
    wechat_open_id: str | None = None
    wechat_union_id: str | None = None
    alipay_open_id: str | None = None


class CreateUserRequest(UserFields):
    pass


class UpdateUserRequest(UserFields):
    pass


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


class RoleInfo(ApiModel):
    id: int
    name: str


class StatusInfo(ApiModel):
    id: int
    name: str


class User(ApiModel):
    id: int
    email: str | None
    provider: str
    first_name: str | None
    last_name: str | None
    phone: str | None
    nickname: str | None
    gender: int | None
    birthday: Date | None
    photo: FileInfo | None
    role: RoleInfo | None
    status: StatusInfo | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    deleted_at: UtcDatetime | None
    wechat_open_id: str | None
    wechat_union_id: str | None
    alipay_open_id: str | None

    @classmethod
    def from_model(cls, user: UserModel, urls: FileUrls) -> User:
        """`user` 须已加载 photo、role、status 关联。"""
        return cls(
            id=user.id,
            email=user.email,
            provider=user.provider,
            first_name=user.first_name,
            last_name=user.last_name,
            phone=user.phone,
            nickname=user.nickname,
            gender=user.gender,
            birthday=user.birthday,
            photo=FileInfo.from_model(user.photo, urls) if user.photo is not None else None,
            role=RoleInfo.model_validate(user.role) if user.role is not None else None,
            status=StatusInfo.model_validate(user.status) if user.status is not None else None,
            created_at=user.created_at,
            updated_at=user.updated_at,
            deleted_at=user.deleted_at,
            wechat_open_id=user.wechat_open_id,
            wechat_union_id=user.wechat_union_id,
            alipay_open_id=user.alipay_open_id,
        )


class UserPage(Page[User]):
    pass
