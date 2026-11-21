from enum import StrEnum

from pydantic import Field, SecretStr

from app.config.base import SettingsGroup


class WechatMiniProgramState(StrEnum):
    DEVELOPER = "developer"
    TRIAL = "trial"
    FORMAL = "formal"


class WechatSettings(SettingsGroup):
    """登录用到的小程序与开放平台（APP）配置。"""

    mini_app_id: str = Field(default="", validation_alias="WECHAT_MINI_APP_ID")
    mini_app_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="WECHAT_MINI_APP_SECRET"
    )
    mini_program_state: WechatMiniProgramState = Field(
        default=WechatMiniProgramState.FORMAL, validation_alias="WECHAT_MINI_PROGRAM_STATE"
    )
    app_id: str = Field(default="", validation_alias="WECHAT_APP_ID")
    app_secret: SecretStr = Field(default=SecretStr(""), validation_alias="WECHAT_APP_SECRET")
