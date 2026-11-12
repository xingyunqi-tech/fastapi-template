from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import Field, SecretStr

from app.config.base import SettingsGroup

_WORD = r"[A-Za-z0-9_]+"
TEMPLATE_FIELDS_PATTERN = rf"^\s*{_WORD}\s*:\s*{_WORD}\s*(,\s*{_WORD}\s*:\s*{_WORD}\s*)*$"


class WechatMiniProgramState(StrEnum):
    DEVELOPER = "developer"
    TRIAL = "trial"
    FORMAL = "formal"


@dataclass(frozen=True, slots=True)
class WechatSubscribeTemplate:
    """订阅消息模板：模板 ID 与「业务字段 → 模板字段 key」映射。"""

    template_id: str
    fields: dict[str, str] = field(default_factory=dict)


def parse_template_fields(value: str | None) -> dict[str, str]:
    """解析 `orderNo:character_string1,amount:amount2` 为 `{orderNo: character_string1, ...}`。"""
    if not value:
        return {}
    pairs = (pair.split(":") for pair in value.split(","))
    return {name.strip(): key.strip() for name, key in pairs}


class WechatSettings(SettingsGroup):
    """nestjs wechat.config 中登录与订阅消息用到的小程序与开放平台（APP）配置。"""

    mini_app_id: str = Field(default="", validation_alias="WECHAT_MINI_APP_ID")
    mini_app_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="WECHAT_MINI_APP_SECRET"
    )
    mini_program_state: WechatMiniProgramState = Field(
        default=WechatMiniProgramState.FORMAL, validation_alias="WECHAT_MINI_PROGRAM_STATE"
    )
    subscribe_order_paid_template_id: str = Field(
        default="", validation_alias="WECHAT_SUBSCRIBE_ORDER_PAID_TEMPLATE_ID"
    )
    subscribe_order_paid_fields: str | None = Field(
        default=None,
        pattern=TEMPLATE_FIELDS_PATTERN,
        validation_alias="WECHAT_SUBSCRIBE_ORDER_PAID_FIELDS",
    )
    app_id: str = Field(default="", validation_alias="WECHAT_APP_ID")
    app_secret: SecretStr = Field(default=SecretStr(""), validation_alias="WECHAT_APP_SECRET")

    @property
    def subscribe_templates(self) -> dict[str, WechatSubscribeTemplate]:
        """按场景配置的订阅消息模板，key 与 `WechatSubscribeScene` 取值一致。"""
        return {
            "orderPaid": WechatSubscribeTemplate(
                self.subscribe_order_paid_template_id,
                parse_template_fields(self.subscribe_order_paid_fields),
            )
        }
