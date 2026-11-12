"""由 scripts/gen_schemas.py 依据 nestjs 基线生成，请勿手改。"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class Address(ContractModel):
    id: int | float = Field(alias="id")
    user_id: int | float = Field(alias="userId")
    name: str = Field(alias="name")
    phone: str = Field(alias="phone")
    province_code: str | None = Field(default=None, alias="provinceCode")
    city_code: str | None = Field(default=None, alias="cityCode")
    district_code: str | None = Field(default=None, alias="districtCode")
    province: str = Field(alias="province")
    city: str = Field(alias="city")
    district: str = Field(alias="district")
    address: str = Field(alias="address")
    is_default: bool = Field(alias="isDefault")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    deleted_at: str = Field(alias="deletedAt")


class ConsentType(StrEnum):
    PRIVACY_POLICY = "PRIVACY_POLICY"
    USER_AGREEMENT = "USER_AGREEMENT"
    AI_SERVICE = "AI_SERVICE"


class AgreeConsentDto(ContractModel):
    type_: ConsentType = Field(alias="type")
    version: str = Field(alias="version")


class AppProfile(ContractModel):
    app_name: str | None = Field(alias="appName")
    slogan: str | None = Field(alias="slogan")
    company_name: str | None = Field(alias="companyName")
    copyright: str | None = Field(alias="copyright")
    icp_license: str | None = Field(alias="icpLicense")
    service_phone: str | None = Field(alias="servicePhone")
    service_hours: str | None = Field(alias="serviceHours")
    service_email: str | None = Field(alias="serviceEmail")
    share_title: str | None = Field(alias="shareTitle")


class AuthAlipayLoginDto(ContractModel):
    auth_code: str = Field(alias="authCode")


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


class AuthPhoneLoginDto(ContractModel):
    phone: str = Field(alias="phone")
    password: str = Field(alias="password")


class AuthPhoneRegisterDto(ContractModel):
    phone: str = Field(alias="phone")
    code: str = Field(alias="code")
    password: str = Field(alias="password")
    nickname: str | None = Field(default=None, alias="nickname")


class AuthPhoneResetPasswordDto(ContractModel):
    phone: str = Field(alias="phone")
    code: str = Field(alias="code")
    password: str = Field(alias="password")


class AuthPhoneSmsLoginDto(ContractModel):
    phone: str = Field(alias="phone")
    code: str = Field(alias="code")


class AuthRegisterLoginDto(ContractModel):
    email: str = Field(alias="email")
    password: str = Field(alias="password")
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")


class AuthResetPasswordDto(ContractModel):
    password: str = Field(alias="password")
    hash: str = Field(alias="hash")


class AuthSendCodeDto(ContractModel):
    phone: str = Field(alias="phone")
    type_: str | None = Field(default=None, alias="type")


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


class AuthWechatLoginDto(ContractModel):
    code: str = Field(alias="code")
    type_: str = Field(alias="type")
    nickname: str | None = Field(default=None, alias="nickname")
    avatar: str | None = Field(default=None, alias="avatar")


class AuthWechatPhoneLoginDto(ContractModel):
    login_code: str = Field(alias="loginCode")
    phone_code: str = Field(alias="phoneCode")


class CheckFavoriteDto(ContractModel):
    target_type: str = Field(alias="targetType")
    target_id: str = Field(alias="targetId")


class CheckFavoriteResponseDto(ContractModel):
    is_favorited: bool = Field(alias="isFavorited")
    favorite_id: str | None = Field(default=None, alias="favoriteId")


class Config(ContractModel):
    key: str = Field(alias="key")
    value: dict[str, Any] = Field(alias="value")
    is_public: bool = Field(alias="isPublic")
    created_at: str | None = Field(alias="createdAt")
    updated_at: str | None = Field(alias="updatedAt")


class ConsentStatusDto(ContractModel):
    type_: ConsentType = Field(alias="type")
    current_version: str | None = Field(alias="currentVersion")
    agreed_version: str | None = Field(alias="agreedVersion")
    agreed_at: str | None = Field(alias="agreedAt")
    agreed: bool = Field(alias="agreed")
    needs_consent: bool = Field(alias="needsConsent")


class CreateAddressDto(ContractModel):
    name: str = Field(alias="name")
    phone: str = Field(alias="phone")
    province_code: str | None = Field(default=None, alias="provinceCode")
    city_code: str | None = Field(default=None, alias="cityCode")
    district_code: str | None = Field(default=None, alias="districtCode")
    province: str | None = Field(default=None, alias="province")
    city: str | None = Field(default=None, alias="city")
    district: str | None = Field(default=None, alias="district")
    address: str = Field(alias="address")
    is_default: bool | None = Field(default=None, alias="isDefault")


class CreateFavoriteDto(ContractModel):
    target_type: str = Field(alias="targetType")
    target_id: str = Field(alias="targetId")
    title: str | None = Field(default=None, alias="title")
    image: str | None = Field(default=None, alias="image")
    extra: str | None = Field(default=None, alias="extra")


class CreateFeedbackDto(ContractModel):
    type_: str = Field(alias="type")
    content: str = Field(alias="content")
    images: list[str] | None = Field(default=None, alias="images")
    contact: str | None = Field(default=None, alias="contact")


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


class Favorite(ContractModel):
    id: int | float = Field(alias="id")
    user_id: int | float = Field(alias="userId")
    target_type: str = Field(alias="targetType")
    target_id: str = Field(alias="targetId")
    title: str | None = Field(default=None, alias="title")
    image: str | None = Field(default=None, alias="image")
    extra: str | None = Field(default=None, alias="extra")
    subject_id: str | None = Field(alias="subjectId")
    created_at: str = Field(alias="createdAt")


class AiMessageSnapshot(ContractModel):
    content: str = Field(alias="content")
    reasoning: str | None = Field(alias="reasoning")
    status: str = Field(alias="status")
    created_at: str = Field(alias="createdAt")


class QuestionReportReason(StrEnum):
    ANSWER_WRONG = "answer_wrong"
    ANALYSIS_WRONG = "analysis_wrong"
    STEM_TYPO = "stem_typo"
    OTHER = "other"


class Feedback(ContractModel):
    id: int | float = Field(alias="id")
    user_id: int | float | None = Field(alias="userId")
    type_: str = Field(alias="type")
    content: str = Field(alias="content")
    images: list[str] | None = Field(default=None, alias="images")
    contact: str | None = Field(default=None, alias="contact")
    status: str = Field(alias="status")
    reply: str | None = Field(default=None, alias="reply")
    question_id: str | None = Field(default=None, alias="questionId")
    question_version: int | float | None = Field(default=None, alias="questionVersion")
    reason: QuestionReportReason | None = Field(default=None, alias="reason")
    errata_id: str | None = Field(default=None, alias="errataId")
    message_id: str | None = Field(default=None, alias="messageId")
    message_snapshot: AiMessageSnapshot | None = Field(default=None, alias="messageSnapshot")
    created_at: str = Field(alias="createdAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")


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


class Notification(ContractModel):
    id: int | float = Field(alias="id")
    user_id: int | float = Field(alias="userId")
    type_: str = Field(alias="type")
    category: str = Field(alias="category")
    title: str = Field(alias="title")
    content: str = Field(alias="content")
    metadata: dict[str, Any] | None = Field(default=None, alias="metadata")
    channels: list[str] = Field(alias="channels")
    sent_channels: dict[str, Any] = Field(alias="sentChannels")
    is_read: bool = Field(alias="isRead")
    read_at: str | None = Field(default=None, alias="readAt")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    deleted_at: str = Field(alias="deletedAt")


class NotificationSetting(ContractModel):
    id: int | float = Field(alias="id")
    user_id: int | float = Field(alias="userId")
    category: str = Field(alias="category")
    enable_in_app: bool = Field(alias="enableInApp")
    enable_email: bool = Field(alias="enableEmail")
    enable_sms: bool = Field(alias="enableSms")
    enable_push: bool = Field(alias="enablePush")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class PaginationAddressResponseDto(ContractModel):
    list_: list[Address] = Field(alias="list")
    total: int | float = Field(alias="total")
    page: int | float = Field(alias="page")
    limit: int | float = Field(alias="limit")
    total_pages: int | float = Field(alias="totalPages")


class FavoriteItemDto(ContractModel):
    id: int | float = Field(alias="id")
    user_id: int | float = Field(alias="userId")
    target_type: str = Field(alias="targetType")
    target_id: str = Field(alias="targetId")
    title: str | None = Field(default=None, alias="title")
    image: str | None = Field(default=None, alias="image")
    extra: str | None = Field(default=None, alias="extra")
    subject_id: str | None = Field(alias="subjectId")
    created_at: str = Field(alias="createdAt")
    target_offline: bool | None = Field(alias="targetOffline")


class PaginationFavoriteItemDtoResponseDto(ContractModel):
    list_: list[FavoriteItemDto] = Field(alias="list")
    total: int | float = Field(alias="total")
    page: int | float = Field(alias="page")
    limit: int | float = Field(alias="limit")
    total_pages: int | float = Field(alias="totalPages")


class PaginationFeedbackResponseDto(ContractModel):
    list_: list[Feedback] = Field(alias="list")
    total: int | float = Field(alias="total")
    page: int | float = Field(alias="page")
    limit: int | float = Field(alias="limit")
    total_pages: int | float = Field(alias="totalPages")


class PaginationNotificationResponseDto(ContractModel):
    list_: list[Notification] = Field(alias="list")
    total: int | float = Field(alias="total")
    page: int | float = Field(alias="page")
    limit: int | float = Field(alias="limit")
    total_pages: int | float = Field(alias="totalPages")


class RefreshResponseDto(ContractModel):
    token: str = Field(alias="token")
    refresh_token: str = Field(alias="refreshToken")
    token_expires: int | float = Field(alias="tokenExpires")


class Region(ContractModel):
    id: int | float = Field(alias="id")
    code: str = Field(alias="code")
    name: str = Field(alias="name")
    level: int | float = Field(alias="level")
    parent_code: str | None = Field(alias="parentCode")
    sort: int | float = Field(alias="sort")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")


class RegionTreeDto(ContractModel):
    id: int | float = Field(alias="id")
    code: str = Field(alias="code")
    name: str = Field(alias="name")
    level: int | float = Field(alias="level")
    parent_code: str | None = Field(alias="parentCode")
    sort: int | float = Field(alias="sort")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    children: list[RegionTreeDto] | None = Field(default=None, alias="children")


class SessionResponseDto(ContractModel):
    id: int | float = Field(alias="id")
    user_agent: str | None = Field(alias="userAgent")
    ip: str | None = Field(alias="ip")
    created_at: str = Field(alias="createdAt")
    last_active_at: str | None = Field(alias="lastActiveAt")
    current: bool = Field(alias="current")


class UpdateAddressDto(ContractModel):
    name: str | None = Field(default=None, alias="name")
    phone: str | None = Field(default=None, alias="phone")
    province_code: str | None = Field(default=None, alias="provinceCode")
    city_code: str | None = Field(default=None, alias="cityCode")
    district_code: str | None = Field(default=None, alias="districtCode")
    province: str | None = Field(default=None, alias="province")
    city: str | None = Field(default=None, alias="city")
    district: str | None = Field(default=None, alias="district")
    address: str | None = Field(default=None, alias="address")
    is_default: bool | None = Field(default=None, alias="isDefault")


class UpdateAppProfileDto(ContractModel):
    app_name: str | None = Field(default=None, alias="appName")
    slogan: str | None = Field(default=None, alias="slogan")
    company_name: str | None = Field(default=None, alias="companyName")
    copyright: str | None = Field(default=None, alias="copyright")
    icp_license: str | None = Field(default=None, alias="icpLicense")
    service_phone: str | None = Field(default=None, alias="servicePhone")
    service_hours: str | None = Field(default=None, alias="serviceHours")
    service_email: str | None = Field(default=None, alias="serviceEmail")
    share_title: str | None = Field(default=None, alias="shareTitle")


class UpdateNotificationSettingsDto(ContractModel):
    category: str = Field(alias="category")
    enable_in_app: bool = Field(alias="enableInApp")
    enable_email: bool = Field(alias="enableEmail")
    enable_sms: bool = Field(alias="enableSms")
    enable_push: bool = Field(alias="enablePush")


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


class UpsertConfigDto(ContractModel):
    value: dict[str, Any] = Field(alias="value")
    is_public: bool | None = Field(default=None, alias="isPublic")
