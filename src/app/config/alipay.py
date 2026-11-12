from enum import StrEnum

from pydantic import Field, SecretStr

from app.config.base import EnvFlag, SettingsGroup

PRODUCTION_GATEWAY = "https://openapi.alipay.com/gateway.do"
SANDBOX_GATEWAY = "https://openapi.alipaydev.com/gateway.do"


class AlipaySignType(StrEnum):
    RSA2 = "RSA2"
    RSA = "RSA"


class AlipaySettings(SettingsGroup):
    """nestjs alipay.config 中登录（alipay.system.oauth.token）用到的部分。"""

    app_id: str = Field(default="", validation_alias="ALIPAY_APP_ID")
    private_key: SecretStr = Field(default=SecretStr(""), validation_alias="ALIPAY_PRIVATE_KEY")
    gateway_url: str | None = Field(default=None, validation_alias="ALIPAY_GATEWAY")
    sign_type: AlipaySignType = Field(
        default=AlipaySignType.RSA2, validation_alias="ALIPAY_SIGN_TYPE"
    )
    charset: str = Field(default="utf-8", validation_alias="ALIPAY_CHARSET")
    sandbox: EnvFlag = Field(default=False, validation_alias="ALIPAY_SANDBOX")

    @property
    def gateway(self) -> str:
        if self.gateway_url:
            return self.gateway_url
        return SANDBOX_GATEWAY if self.sandbox else PRODUCTION_GATEWAY
