from pydantic import Field, SecretStr

from app.config.base import SettingsGroup


class WechatSettings(SettingsGroup):
    """小程序（`wechat_miniapp`）与开放平台移动应用（`wechat_app`）；AppID 与密钥配齐即启用。"""

    miniapp_app_id: str = Field(default="", validation_alias="WECHAT_MINIAPP_APP_ID")
    miniapp_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="WECHAT_MINIAPP_SECRET"
    )
    app_app_id: str = Field(default="", validation_alias="WECHAT_APP_APP_ID")
    app_secret: SecretStr = Field(default=SecretStr(""), validation_alias="WECHAT_APP_SECRET")

    @property
    def miniapp_enabled(self) -> bool:
        return bool(self.miniapp_app_id and self.miniapp_secret.get_secret_value())

    @property
    def app_enabled(self) -> bool:
        return bool(self.app_app_id and self.app_secret.get_secret_value())
