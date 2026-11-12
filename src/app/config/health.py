from pydantic import Field

from app.config.base import SettingsGroup


class HealthSettings(SettingsGroup):
    """健康检查判断微信 / 支付宝是否已配置所需的变量（与 nestjs 同名）。"""

    wechat_app_id: str = Field(default="", validation_alias="WECHAT_APP_ID")
    wechat_app_secret: str = Field(default="", validation_alias="WECHAT_APP_SECRET")
    alipay_app_id: str = Field(default="", validation_alias="ALIPAY_APP_ID")
