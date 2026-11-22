import re
from datetime import timedelta
from typing import Annotated, Any, Final

from pydantic import AliasChoices, BeforeValidator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_env_flag(value: Any) -> Any:
    if value == "true":
        return True
    if value == "false":
        return False
    if isinstance(value, str):
        raise ValueError('must be "true" or "false"')
    return value


EnvFlag = Annotated[bool, BeforeValidator(_parse_env_flag)]
"""只接受 `true` / `false`，空串视为未设置。"""

_DURATION_PART: Final = re.compile(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h)")
_DURATION_UNIT: Final = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}


def _parse_duration(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    parts = _DURATION_PART.findall(value)
    if not parts or "".join(number + unit for number, unit in parts) != value:
        raise ValueError("must be a duration such as 10s, 15m or 720h")
    return timedelta(seconds=sum(float(number) * _DURATION_UNIT[unit] for number, unit in parts))


Duration = Annotated[timedelta, BeforeValidator(_parse_duration)]
"""约定 §9 的时长格式：`ms` / `s` / `m` / `h` 组合，如 `10s`、`1h30m`。"""


class SettingsGroup(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=True,
        extra="ignore",
    )


def env_names(group: type[SettingsGroup]) -> set[str]:
    names: set[str] = set()
    for field in group.model_fields.values():
        alias = field.validation_alias
        if isinstance(alias, str):
            names.add(alias)
        elif isinstance(alias, AliasChoices):
            names.update(choice for choice in alias.choices if isinstance(choice, str))
    return names
