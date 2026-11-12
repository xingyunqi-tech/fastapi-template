from pydantic import Field

from app.config.base import EnvFlag, SettingsGroup


class ThrottlerSettings(SettingsGroup):
    """nestjs throttler.config：TTL 以秒配置，限流键与规则见 infrastructure/throttler。"""

    enabled: EnvFlag = Field(default=True, validation_alias="THROTTLE_ENABLED")
    ttl: int = Field(default=60, ge=1, validation_alias="THROTTLE_TTL")
    limit: int = Field(default=100, ge=1, validation_alias="THROTTLE_LIMIT")

    @property
    def ttl_ms(self) -> int:
        return self.ttl * 1000
