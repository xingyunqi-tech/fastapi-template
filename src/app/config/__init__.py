from dataclasses import dataclass
from functools import cache

from pydantic import ValidationError

from app.config.alipay import AlipaySettings
from app.config.app import AppSettings, Environment
from app.config.auth import AuthSettings
from app.config.base import SettingsGroup
from app.config.database import DatabaseSettings
from app.config.events import EventsSettings, EventsTransport
from app.config.log import LogFormat, LogLevel, LogSettings
from app.config.mail import MailSettings, MailTls
from app.config.rate_limit import Quota, RateLimitSettings
from app.config.redis import RedisSettings
from app.config.sms import SmsSettings
from app.config.storage import StorageDriver, StorageSettings
from app.config.telemetry import MetricsExporter, TelemetrySettings, TracesExporter
from app.config.wechat import WechatSettings

__all__ = [
    "AlipaySettings",
    "AppSettings",
    "AuthSettings",
    "DatabaseSettings",
    "Environment",
    "EventsSettings",
    "EventsTransport",
    "LogFormat",
    "LogLevel",
    "LogSettings",
    "MailSettings",
    "MailTls",
    "MetricsExporter",
    "Quota",
    "RateLimitSettings",
    "RedisSettings",
    "Settings",
    "SettingsError",
    "SmsSettings",
    "StorageDriver",
    "StorageSettings",
    "TelemetrySettings",
    "TracesExporter",
    "WechatSettings",
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
    storage: StorageSettings
    rate_limit: RateLimitSettings
    log: LogSettings
    telemetry: TelemetrySettings
    events: EventsSettings
    sms: SmsSettings
    wechat: WechatSettings
    alipay: AlipaySettings

    @classmethod
    def load(cls) -> Settings:
        loader = _Loader()
        groups = {
            name: loader.load(group)
            for name, group in (
                ("app", AppSettings),
                ("auth", AuthSettings),
                ("database", DatabaseSettings),
                ("redis", RedisSettings),
                ("mail", MailSettings),
                ("storage", StorageSettings),
                ("rate_limit", RateLimitSettings),
                ("log", LogSettings),
                ("telemetry", TelemetrySettings),
                ("events", EventsSettings),
                ("sms", SmsSettings),
                ("wechat", WechatSettings),
                ("alipay", AlipaySettings),
            )
        }
        if loader.errors or any(group is None for group in groups.values()):
            raise SettingsError(loader.errors)
        settings = cls(**groups)  # type: ignore[arg-type]
        errors = settings.cross_check()
        if errors:
            raise SettingsError(errors)
        return settings

    def cross_check(self) -> list[str]:
        """跨分组校验：签发方必填项与生产环境必填项。"""
        errors: list[str] = []
        if self.storage.driver is None:
            errors.append("STORAGE_DRIVER: required for the issuer")
        if self.events.transport is EventsTransport.REDIS_STREAM and not self.redis.enabled:
            errors.append("REDIS_URL: required when EVENTS_TRANSPORT=redis_stream")
        if self.telemetry.prometheus_enabled and self.app.web_concurrency > 1:
            errors.append("WEB_CONCURRENCY: must be 1 when OTEL_METRICS_EXPORTER=prometheus")
        if not self.app.is_production:
            return errors
        if not self.app.frontend_url:
            errors.append("APP_FRONTEND_URL: required in production")
        if not self.redis.enabled:
            errors.append("REDIS_URL: required in production")
        if not self.app.cors_origins:
            errors.append("CORS_ORIGINS: required in production")
        if self.telemetry.prometheus_enabled and self.telemetry.metrics_token is None:
            errors.append(
                "METRICS_TOKEN: required in production with OTEL_METRICS_EXPORTER=prometheus"
            )
        if self.storage.driver is StorageDriver.LOCAL and self.storage.url_secret is None:
            errors.append("STORAGE_URL_SECRET: required in production with STORAGE_DRIVER=local")
        errors.extend(self.mail.validate_for_production())
        return errors


@cache
def get_settings() -> Settings:
    return Settings.load()
