"""认证接口的请求与响应模型。"""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import StringConstraints

from app.common.types import ApiModel, Date, Email, UtcDatetime
from app.core.users.schemas import (
    CnMobile,
    FileRef,
    Gender,
    Md5Password,
    Nickname,
    NotEmpty,
    Sanitized,
    User,
)
from app.integrations.sms import SmsCodeType

SmsCode = Annotated[str, StringConstraints(min_length=4, max_length=8)]


class WechatLoginType(StrEnum):
    MINI_APP = "mini_app"
    APP = "app"


class EmailLoginRequest(ApiModel):
    email: Email
    password: Md5Password


class EmailRegisterRequest(ApiModel):
    email: Email
    password: Md5Password
    first_name: Annotated[Sanitized, StringConstraints(min_length=1)]
    last_name: Annotated[Sanitized, StringConstraints(min_length=1)]


class ConfirmEmailRequest(ApiModel):
    hash: NotEmpty


class ForgotPasswordRequest(ApiModel):
    email: Email


class ResetPasswordRequest(ApiModel):
    password: Md5Password
    hash: NotEmpty


class UpdateMeRequest(ApiModel):
    photo: FileRef | None = None
    first_name: Annotated[Sanitized, StringConstraints(min_length=1)] | None = None
    last_name: Annotated[Sanitized, StringConstraints(min_length=1)] | None = None
    nickname: Annotated[Nickname, StringConstraints(min_length=1)] | None = None
    gender: Gender | None = None
    birthday: Date | None = None
    email: Email | None = None
    password: Md5Password | None = None
    old_password: Md5Password | None = None
    sms_code: SmsCode | None = None


class DeleteAccountRequest(ApiModel):
    old_password: Md5Password | None = None
    sms_code: SmsCode | None = None


class SendSmsCodeRequest(ApiModel):
    phone: CnMobile
    type: SmsCodeType | None = None


class PhoneLoginRequest(ApiModel):
    phone: CnMobile
    password: Md5Password


class PhoneSmsLoginRequest(ApiModel):
    phone: CnMobile
    code: SmsCode


class PhoneRegisterRequest(ApiModel):
    phone: CnMobile
    code: SmsCode
    password: Md5Password
    nickname: Nickname | None = None


class PhoneResetPasswordRequest(ApiModel):
    phone: CnMobile
    code: Annotated[str, StringConstraints(pattern=r"^[0-9]{4,8}$")]
    password: Md5Password


class ChangePasswordRequest(ApiModel):
    old_password: Md5Password
    new_password: Md5Password


class WechatLoginRequest(ApiModel):
    code: NotEmpty
    type: WechatLoginType
    nickname: str | None = None
    avatar: str | None = None


class WechatPhoneLoginRequest(ApiModel):
    login_code: NotEmpty
    phone_code: NotEmpty


class AlipayLoginRequest(ApiModel):
    auth_code: NotEmpty


class TokenPair(ApiModel):
    token: str
    refresh_token: str
    token_expires: int


class LoginData(TokenPair):
    user: User


class Session(ApiModel):
    id: UUID
    user_agent: str | None
    ip: str | None
    created_at: UtcDatetime
    last_active_at: UtcDatetime | None
    current: bool
