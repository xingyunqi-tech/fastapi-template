"""约定 §11.1 请求与 §5.3 登录响应。"""

from typing import Annotated, Literal

from pydantic import StringConstraints

from app.common.types import ApiModel, Email, Phone
from app.identity.providers.sms import SmsScene
from app.identity.users.schemas import DisplayName, Password, User
from app.security.passwords import PASSWORD_MAX_LENGTH

SubmittedPassword = Annotated[str, StringConstraints(min_length=1, max_length=PASSWORD_MAX_LENGTH)]
OpaqueToken = Annotated[str, StringConstraints(min_length=1, max_length=256)]
SmsCode = Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]
ProviderCode = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class RegisterWithEmailRequest(ApiModel):
    email: Email
    password: Password
    display_name: DisplayName | None = None


class LoginWithEmailRequest(ApiModel):
    email: Email
    password: SubmittedPassword


class VerifyEmailRequest(ApiModel):
    token: OpaqueToken


class ForgotPasswordRequest(ApiModel):
    email: Email


class ResetPasswordRequest(ApiModel):
    token: OpaqueToken
    password: Password


class SendSmsCodeRequest(ApiModel):
    phone: Phone
    scene: SmsScene


class LoginWithSmsRequest(ApiModel):
    phone: Phone
    code: SmsCode


class AuthProvider(ApiModel):
    id: str


class LoginWithProviderRequest(ApiModel):
    code: ProviderCode


class RefreshTokenRequest(ApiModel):
    refresh_token: OpaqueToken


class LoginResponse(ApiModel):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105
    user: User
