from dataclasses import dataclass
from functools import cache

from pydantic import ValidationError

from app.config.app import AppSettings, Environment
from app.config.base import SettingsGroup

__all__ = ["AppSettings", "Environment", "Settings", "SettingsError", "get_settings"]


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

    @classmethod
    def load(cls) -> Settings:
        loader = _Loader()
        app = loader.load(AppSettings)
        if loader.errors or app is None:
            raise SettingsError(loader.errors)
        return cls(app=app)


@cache
def get_settings() -> Settings:
    return Settings.load()
