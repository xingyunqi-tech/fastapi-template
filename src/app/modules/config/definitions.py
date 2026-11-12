"""远程配置项登记表（nestjs config-definitions.ts），配置值统一存为 `{"value": <配置值>}`。"""

from dataclasses import dataclass
from typing import Any, Final, Literal

type ConfigValueType = Literal["boolean", "number", "string", "json"]

NO_DEFAULT: Final[Any] = object()


@dataclass(frozen=True, slots=True)
class ConfigDefinition:
    key: str
    type: ConfigValueType
    is_public: bool
    description: str
    default_value: Any = NO_DEFAULT
    dedicated: bool = False
    """只能经专用接口写入，通用写接口拒绝。"""

    @property
    def has_default(self) -> bool:
        return self.default_value is not NO_DEFAULT


CONFIG_DEFINITIONS: Final = (
    ConfigDefinition("ai.enabled", "boolean", True, "AI 功能总开关", True),
    ConfigDefinition("ai.dailyBudget", "number", False, "全站每日成本上限（元），超过即熔断"),
    ConfigDefinition("ai.quota.chat", "number", True, "用户每日讲题/对话次数", 20),
    ConfigDefinition("ai.quota.grading", "number", True, "用户每日批改次数", 3),
    ConfigDefinition("ai.ratePerMinute", "number", False, "每用户每分钟 AI 请求上限", 10),
    ConfigDefinition("ai.paperGradingLimit", "number", False, "每份试卷自动批改主观题上限", 5),
    ConfigDefinition("ai.model", "string", False, "当前使用的模型标识"),
    ConfigDefinition("wrongBook.masterThreshold", "number", True, "错题连续答对阈值", 3),
    ConfigDefinition(
        "practice.offlineSyncWindowMin", "number", False, "模考离线补交窗口（分钟）", 5
    ),
    ConfigDefinition("practice.dailySetSize", "number", False, "每日一练题量", 10),
    ConfigDefinition("practice.trialSize", "number", True, "游客试做题数", 3),
    ConfigDefinition("paper.generateDailyLimit", "number", True, "每用户每日按规则生成试卷次数", 5),
    ConfigDefinition("consent.privacyPolicyVersion", "string", True, "隐私政策当前版本"),
    ConfigDefinition("consent.userAgreementVersion", "string", True, "用户协议当前版本"),
    ConfigDefinition("consent.aiServiceVersion", "string", True, "AI 服务声明当前版本"),
    ConfigDefinition(
        "agreement.userAgreement",
        "string",
        True,
        "用户协议正文（HTML），未配置时小程序显示内置文本",
    ),
    ConfigDefinition(
        "agreement.privacyPolicy",
        "string",
        True,
        "隐私政策正文（HTML），未配置时小程序显示内置文本",
    ),
    ConfigDefinition(
        "agreement.aiService",
        "string",
        True,
        "AI 服务声明正文（HTML），未配置时小程序显示内置文本",
    ),
    ConfigDefinition(
        "app.profile",
        "json",
        False,
        "小程序站点信息（产品名称、主体、客服等），未配置的字段由小程序使用内置值",
        dedicated=True,
    ),
    ConfigDefinition("alert.thresholds", "json", False, "各监控项告警阈值"),
)

_BY_KEY: Final = {definition.key: definition for definition in CONFIG_DEFINITIONS}


def find_config_definition(key: str) -> ConfigDefinition | None:
    return _BY_KEY.get(key)
