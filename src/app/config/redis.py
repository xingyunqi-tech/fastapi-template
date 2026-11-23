from pydantic import Field, SecretStr

from app.config.base import SettingsGroup


class RedisSettings(SettingsGroup):
    """`REDIS_URL` 未设置时缓存、锁、限流、吊销、短信计数与队列用进程内实现。"""

    redis_url: SecretStr | None = Field(default=None, validation_alias="REDIS_URL")

    @property
    def enabled(self) -> bool:
        return self.redis_url is not None

    def url(self) -> str:
        if self.redis_url is None:
            raise RuntimeError("REDIS_URL is not set")
        return self.redis_url.get_secret_value()
