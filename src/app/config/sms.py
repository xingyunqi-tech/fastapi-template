from pydantic import Field, SecretStr

from app.config.base import EnvFlag, SettingsGroup


class SmsSettings(SettingsGroup):
    """nestjs sms.config：阿里云短信与验证码规则，时间单位为秒。"""

    access_key_id: str = Field(default="", validation_alias="SMS_ACCESS_KEY_ID")
    access_key_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="SMS_ACCESS_KEY_SECRET"
    )
    sign_name: str = Field(default="", validation_alias="SMS_SIGN_NAME")
    template_code: str = Field(default="", validation_alias="SMS_TEMPLATE_CODE")
    code_expires: int = Field(default=300, ge=60, le=600, validation_alias="SMS_CODE_EXPIRES")
    code_length: int = Field(default=6, ge=4, le=8, validation_alias="SMS_CODE_LENGTH")
    endpoint: str = Field(default="dysmsapi.aliyuncs.com", validation_alias="SMS_ENDPOINT")
    mock_mode: EnvFlag = Field(default=False, validation_alias="SMS_MOCK_MODE")
    max_attempts: int = Field(default=5, ge=1, le=10, validation_alias="SMS_MAX_ATTEMPTS")
    resend_interval: int = Field(default=60, ge=30, le=300, validation_alias="SMS_RESEND_INTERVAL")
