"""由 scripts/gen_schemas.py 依据 nestjs 基线生成，请勿手改。"""

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AuthChangePasswordDto(ContractModel):
    old_password: str = Field(alias="oldPassword")
    new_password: str = Field(alias="newPassword")


class AuthConfirmEmailDto(ContractModel):
    hash: str = Field(alias="hash")


class AuthDeleteAccountDto(ContractModel):
    old_password: str | None = Field(default=None, alias="oldPassword")
    sms_code: str | None = Field(default=None, alias="smsCode")


class AuthEmailLoginDto(ContractModel):
    email: str = Field(alias="email")
    password: str = Field(alias="password")


class AuthForgotPasswordDto(ContractModel):
    email: str = Field(alias="email")


class AuthRegisterLoginDto(ContractModel):
    email: str = Field(alias="email")
    password: str = Field(alias="password")
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")


class AuthResetPasswordDto(ContractModel):
    password: str = Field(alias="password")
    hash: str = Field(alias="hash")


class FileDto(ContractModel):
    id: str = Field(alias="id")


class AuthUpdateDto(ContractModel):
    photo: FileDto | None = Field(default=None, alias="photo")
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    nickname: str | None = Field(default=None, alias="nickname")
    gender: int | float | None = Field(default=None, alias="gender")
    birthday: str | None = Field(default=None, alias="birthday")
    email: str | None = Field(default=None, alias="email")
    password: str | None = Field(default=None, alias="password")
    old_password: str | None = Field(default=None, alias="oldPassword")
    sms_code: str | None = Field(default=None, alias="smsCode")


class RoleDto(ContractModel):
    id: int | float = Field(alias="id")


class StatusDto(ContractModel):
    id: int | float = Field(alias="id")


class CreateUserDto(ContractModel):
    email: str | None = Field(default=None, alias="email")
    password: str | None = Field(default=None, alias="password")
    provider: str | None = Field(default=None, alias="provider")
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    phone: str | None = Field(default=None, alias="phone")
    nickname: str | None = Field(default=None, alias="nickname")
    gender: int | float | None = Field(default=None, alias="gender")
    birthday: str | None = Field(default=None, alias="birthday")
    photo: FileDto | None = Field(default=None, alias="photo")
    role: RoleDto | None = Field(default=None, alias="role")
    status: StatusDto | None = Field(default=None, alias="status")
    wechat_open_id: str | None = Field(default=None, alias="wechatOpenId")
    wechat_union_id: str | None = Field(default=None, alias="wechatUnionId")
    alipay_open_id: str | None = Field(default=None, alias="alipayOpenId")


class FileType(ContractModel):
    id: str = Field(alias="id")
    path: str = Field(alias="path")
    visibility: str | None = Field(default=None, alias="visibility")
    mime_type: str | None = Field(default=None, alias="mimeType")
    size: int | float | None = Field(default=None, alias="size")


class Role(ContractModel):
    id: int | float = Field(alias="id")
    name: str = Field(alias="name")


class Status(ContractModel):
    id: int | float = Field(alias="id")
    name: str = Field(alias="name")


class User(ContractModel):
    id: int | float = Field(alias="id")
    email: str = Field(alias="email")
    provider: str = Field(alias="provider")
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")
    phone: str = Field(alias="phone")
    nickname: str = Field(alias="nickname")
    gender: int | float = Field(alias="gender")
    birthday: str = Field(alias="birthday")
    photo: FileType = Field(alias="photo")
    role: Role = Field(alias="role")
    status: Status = Field(alias="status")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    deleted_at: str = Field(alias="deletedAt")
    wechat_open_id: str = Field(alias="wechatOpenId")
    wechat_union_id: str = Field(alias="wechatUnionId")
    alipay_open_id: str = Field(alias="alipayOpenId")


class InfinityPaginationUserResponseDto(ContractModel):
    data: list[User] = Field(alias="data")
    has_next_page: bool = Field(alias="hasNextPage")


class LoginResponseDto(ContractModel):
    token: str = Field(alias="token")
    refresh_token: str = Field(alias="refreshToken")
    token_expires: int | float = Field(alias="tokenExpires")
    user: User = Field(alias="user")


class RefreshResponseDto(ContractModel):
    token: str = Field(alias="token")
    refresh_token: str = Field(alias="refreshToken")
    token_expires: int | float = Field(alias="tokenExpires")


class SessionResponseDto(ContractModel):
    id: int | float = Field(alias="id")
    user_agent: str | None = Field(alias="userAgent")
    ip: str | None = Field(alias="ip")
    created_at: str = Field(alias="createdAt")
    last_active_at: str | None = Field(alias="lastActiveAt")
    current: bool = Field(alias="current")


class UpdateUserDto(ContractModel):
    email: str | None = Field(default=None, alias="email")
    password: str | None = Field(default=None, alias="password")
    provider: str | None = Field(default=None, alias="provider")
    first_name: str | None = Field(default=None, alias="firstName")
    last_name: str | None = Field(default=None, alias="lastName")
    phone: str | None = Field(default=None, alias="phone")
    nickname: str | None = Field(default=None, alias="nickname")
    gender: int | float | None = Field(default=None, alias="gender")
    birthday: str | None = Field(default=None, alias="birthday")
    photo: FileDto | None = Field(default=None, alias="photo")
    role: RoleDto | None = Field(default=None, alias="role")
    status: StatusDto | None = Field(default=None, alias="status")
    wechat_open_id: str | None = Field(default=None, alias="wechatOpenId")
    wechat_union_id: str | None = Field(default=None, alias="wechatUnionId")
    alipay_open_id: str | None = Field(default=None, alias="alipayOpenId")
