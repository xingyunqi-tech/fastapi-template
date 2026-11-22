from enum import StrEnum
from typing import Self

from pydantic import Field, SecretStr, model_validator

from app.config.base import SettingsGroup


class SmsProvider(StrEnum):
    LOG = "log"
    ALIYUN = "aliyun"


class SmsSettings(SettingsGroup):
    """短信发送渠道；验证码规则由约定 §11.1 固定。"""

    provider: SmsProvider = Field(default=SmsProvider.LOG, validation_alias="SMS_PROVIDER")
    aliyun_access_key_id: str = Field(default="", validation_alias="SMS_ALIYUN_ACCESS_KEY_ID")
    aliyun_access_key_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="SMS_ALIYUN_ACCESS_KEY_SECRET"
    )
    aliyun_sign_name: str = Field(default="", validation_alias="SMS_ALIYUN_SIGN_NAME")
    aliyun_template_code: str = Field(default="", validation_alias="SMS_ALIYUN_TEMPLATE_CODE")
    aliyun_endpoint: str = Field(
        default="dysmsapi.aliyuncs.com", validation_alias="SMS_ALIYUN_ENDPOINT"
    )

    @model_validator(mode="after")
    def _aliyun_complete(self) -> Self:
        if self.provider is SmsProvider.ALIYUN and not (
            self.aliyun_access_key_id
            and self.aliyun_access_key_secret.get_secret_value()
            and self.aliyun_sign_name
            and self.aliyun_template_code
        ):
            raise ValueError(
                "SMS_PROVIDER=aliyun requires SMS_ALIYUN_ACCESS_KEY_ID, "
                "SMS_ALIYUN_ACCESS_KEY_SECRET, SMS_ALIYUN_SIGN_NAME and SMS_ALIYUN_TEMPLATE_CODE"
            )
        return self
