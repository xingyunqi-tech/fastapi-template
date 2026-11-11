from typing import Annotated, Any

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
"""对应 nestjs validate-config：只接受 `true` / `false`，空串视为未设置。"""


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
