"""小程序站点信息（nestjs AppProfileService）：存于配置项 `app.profile`，整体覆盖。"""

from typing import Any, Final

from app.common.validation.js import trim
lazy from app.modules.config.service import AppConfigService

APP_PROFILE_CONFIG_KEY: Final = "app.profile"
APP_PROFILE_FIELDS: Final = (
    "appName",
    "slogan",
    "companyName",
    "copyright",
    "icpLicense",
    "servicePhone",
    "serviceHours",
    "serviceEmail",
    "shareTitle",
)

type AppProfile = dict[str, str | None]


def to_profile(source: Any) -> AppProfile:
    record = source if isinstance(source, dict) else {}
    profile: AppProfile = {}
    for field in APP_PROFILE_FIELDS:
        value = record.get(field)
        profile[field] = trim(value) if isinstance(value, str) and trim(value) else None
    return profile


class AppProfileService:
    def __init__(self, config: AppConfigService) -> None:
        self.config = config

    async def get(self) -> AppProfile:
        return to_profile(await self.config.get_value(APP_PROFILE_CONFIG_KEY))

    async def update(self, data: dict[str, Any]) -> AppProfile:
        """整体覆盖，未传的字段保存为 null。"""
        profile = to_profile(data)
        await self.config.upsert(APP_PROFILE_CONFIG_KEY, {"value": profile})
        return profile
