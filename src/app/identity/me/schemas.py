from uuid import UUID

from app.common.types import ApiModel, Phone
from app.identity.auth.schemas import ProviderCode, SmsCode, SubmittedPassword
from app.identity.users.schemas import DisplayName, Password


class UpdateMeRequest(ApiModel):
    display_name: DisplayName | None = None
    avatar_file_id: UUID | None = None


class ChangePasswordRequest(ApiModel):
    current_password: SubmittedPassword | None = None
    new_password: Password


class BindPhoneRequest(ApiModel):
    phone: Phone
    code: SmsCode


class BindPhoneWithWechatRequest(ApiModel):
    code: ProviderCode
