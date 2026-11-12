from dataclasses import dataclass
from functools import cache

from pydantic import ValidationError

from app.config.app import AppSettings, Environment
from app.config.auth import AuthSettings, PasswordHasher
from app.config.base import SettingsGroup
from app.config.database import DatabaseSettings, SchemaOwner
from app.config.mail import MailSettings
from app.config.redis import RedisSettings

__all__ = [
    "AppSettings",
    "AuthSettings",
    "DatabaseSettings",
    "Environment",
    "MailSettings",
    "PasswordHasher",
    "RedisSettings",
    "SchemaOwner",
    "Settings",
    "SettingsError",
    "get_settings",
]


class SettingsError(RuntimeError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("配置校验失败:\n" + "\n".join(f"  - {e}" for e in errors))
        self.errors = errors


class _Loader:
    """逐组加载配置并收集错误，全部分组校验完再统一失败。"""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def load[T: SettingsGroup](self, group: type[T]) -> T | None:
        try:
            return group()
        except ValidationError as exc:
            self.errors.extend(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
            )
            return None


@dataclass(frozen=True, slots=True)
class Settings:
    app: AppSettings
    auth: AuthSettings
    database: DatabaseSettings
    redis: RedisSettings
    mail: MailSettings

    @classmethod
    def load(cls) -> Settings:
        loader = _Loader()
        app = loader.load(AppSettings)
        auth = loader.load(AuthSettings)
        database = loader.load(DatabaseSettings)
        redis = loader.load(RedisSettings)
        mail = loader.load(MailSettings)
        if app is not None and app.is_production:
            if auth is not None:
                loader.errors.extend(auth.validate_for_production())
            if redis is not None and not redis.enabled:
                loader.errors.append("REDIS_ENABLED: must be true in production")
        if (
            loader.errors
            or app is None
            or auth is None
            or database is None
            or redis is None
            or mail is None
        ):
            raise SettingsError(loader.errors)
        return cls(app=app, auth=auth, database=database, redis=redis, mail=mail)


@cache
def get_settings() -> Settings:
    return Settings.load()
