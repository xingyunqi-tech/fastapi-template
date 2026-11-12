"""通知 metadata.wechatSubscribe 到微信订阅消息的映射（nestjs wechat-subscribe）。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
lazy from collections.abc import Mapping

from app.integrations.wechat.subscribe_value import format_subscribe_value
lazy from app.config.wechat import WechatSubscribeTemplate


class WechatSubscribeScene(StrEnum):
    """取值与 `WechatSettings.subscribe_templates` 的 key 一致。"""

    ORDER_PAID = "orderPaid"


@dataclass(frozen=True, slots=True)
class BuiltSubscribeMessage:
    template_id: str
    page: str | None
    data: dict[str, str]


def build_subscribe_message(
    payload: Mapping[str, Any], template: WechatSubscribeTemplate | None
) -> BuiltSubscribeMessage | None:
    """按模板配置把业务字段映射为模板字段，模板未配置时返回 None。"""
    if template is None or not template.template_id:
        return None
    values: Mapping[str, Any] = payload.get("values") or {}
    data = {
        key: format_subscribe_value(key, values[field])
        for field, key in template.fields.items()
        if field in values
    }
    return BuiltSubscribeMessage(template.template_id, payload.get("page"), data)
