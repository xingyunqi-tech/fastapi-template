from enum import StrEnum

from pydantic import Field, SecretStr, field_validator

from app.config.base import EnvFlag, SettingsGroup
from app.identity.providers.alipay.keys import load_private_key, load_public_key

PRODUCTION_GATEWAY = "https://openapi.alipay.com/gateway.do"
SANDBOX_GATEWAY = "https://openapi.alipaydev.com/gateway.do"


class AlipaySignType(StrEnum):
    RSA2 = "RSA2"
    RSA = "RSA"


class AlipaySettings(SettingsGroup):
    """`alipay.system.oauth.token` 登录；AppID、应用私钥与支付宝公钥配齐即启用。"""

    app_id: str = Field(default="", validation_alias="ALIPAY_APP_ID")
    private_key: SecretStr = Field(default=SecretStr(""), validation_alias="ALIPAY_PRIVATE_KEY")
    public_key: str = Field(default="", validation_alias="ALIPAY_PUBLIC_KEY")
    gateway_url: str | None = Field(default=None, validation_alias="ALIPAY_GATEWAY")
    sign_type: AlipaySignType = Field(
        default=AlipaySignType.RSA2, validation_alias="ALIPAY_SIGN_TYPE"
    )
    charset: str = Field(default="utf-8", validation_alias="ALIPAY_CHARSET")
    sandbox: EnvFlag = Field(default=False, validation_alias="ALIPAY_SANDBOX")

    @field_validator("private_key")
    @classmethod
    def _private_key(cls, value: SecretStr) -> SecretStr:
        if value.get_secret_value():
            try:
                load_private_key(value.get_secret_value())
            except TypeError, ValueError:
                raise ValueError("must be an RSA private key (PKCS#8)") from None
        return value

    @field_validator("public_key")
    @classmethod
    def _public_key(cls, value: str) -> str:
        if value:
            try:
                load_public_key(value)
            except TypeError, ValueError:
                raise ValueError("must be an RSA public key") from None
        return value

    @property
    def enabled(self) -> bool:
        return bool(self.app_id and self.private_key.get_secret_value() and self.public_key)

    @property
    def gateway(self) -> str:
        if self.gateway_url:
            return self.gateway_url
        return SANDBOX_GATEWAY if self.sandbox else PRODUCTION_GATEWAY
