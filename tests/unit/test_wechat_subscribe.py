import pytest

from app.config import SettingsError, WechatSettings, get_settings
from app.config.wechat import WechatMiniProgramState, WechatSubscribeTemplate
from app.integrations.wechat.subscribe_value import format_subscribe_value
from app.modules.notifications.wechat_subscribe import (
    BuiltSubscribeMessage,
    WechatSubscribeScene,
    build_subscribe_message,
)

PAYLOAD = {
    "scene": WechatSubscribeScene.ORDER_PAID.value,
    "values": {"orderNo": "NO1", "amount": "99.00元", "paidAt": "2026年10月14日 15:01"},
    "page": "pages-sub/order/detail/index?id=1",
}


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("character_string1", "A" * 40, "A" * 32),
        ("thing2", "商品" * 15, "商品" * 10),
        ("phrase3", "😀😀😀😀😀😀", "😀😀😀😀😀"),
        ("name4", "abcdefghijkl", "abcdefghij"),
        ("amount5", "1" * 50, "1" * 50),
        ("time6", "2026年10月14日 15:01", "2026年10月14日 15:01"),
    ],
)
def test_format_subscribe_value_truncates_by_field_type(
    key: str, value: str, expected: str
) -> None:
    assert format_subscribe_value(key, value) == expected


def test_build_subscribe_message_maps_configured_fields() -> None:
    template = WechatSubscribeTemplate(
        "TPL", {"orderNo": "character_string1", "amount": "amount2", "missing": "thing9"}
    )
    assert build_subscribe_message(PAYLOAD, template) == BuiltSubscribeMessage(
        template_id="TPL",
        page="pages-sub/order/detail/index?id=1",
        data={"character_string1": "NO1", "amount2": "99.00元"},
    )


@pytest.mark.parametrize(
    "template", [None, WechatSubscribeTemplate("", {"orderNo": "character_string1"})]
)
def test_build_subscribe_message_returns_none_without_template_id(
    template: WechatSubscribeTemplate | None,
) -> None:
    assert build_subscribe_message(PAYLOAD, template) is None


def test_subscribe_settings_defaults() -> None:
    settings = WechatSettings()
    assert settings.mini_program_state is WechatMiniProgramState.FORMAL
    assert settings.subscribe_templates == {"orderPaid": WechatSubscribeTemplate("", {})}


def test_subscribe_templates_parse_field_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WECHAT_MINI_PROGRAM_STATE", "trial")
    monkeypatch.setenv("WECHAT_SUBSCRIBE_ORDER_PAID_TEMPLATE_ID", "TPL")
    monkeypatch.setenv(
        "WECHAT_SUBSCRIBE_ORDER_PAID_FIELDS", " orderNo : character_string1,amount:amount2 "
    )
    settings = WechatSettings()
    assert settings.mini_program_state is WechatMiniProgramState.TRIAL
    assert settings.subscribe_templates == {
        "orderPaid": WechatSubscribeTemplate(
            "TPL", {"orderNo": "character_string1", "amount": "amount2"}
        )
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WECHAT_SUBSCRIBE_ORDER_PAID_FIELDS", "orderNo=character_string1"),
        ("WECHAT_SUBSCRIBE_ORDER_PAID_FIELDS", "orderNo:character_string1,"),
        ("WECHAT_SUBSCRIBE_ORDER_PAID_FIELDS", "订单:thing1"),
        ("WECHAT_MINI_PROGRAM_STATE", "release"),
    ],
)
def test_invalid_subscribe_settings_fail_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(SettingsError):
        get_settings()
