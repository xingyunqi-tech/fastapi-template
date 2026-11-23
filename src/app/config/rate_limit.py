import math
import re
from dataclasses import dataclass
from typing import Annotated, Any, Final

from pydantic import BeforeValidator, Field
from pydantic_settings import NoDecode

from app.config.base import SettingsGroup

_RULE: Final = re.compile(r"([1-9][0-9]*)/(ms|s|m|h),burst=([1-9][0-9]*)")
_PERIOD_MS: Final = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000}


@dataclass(frozen=True, slots=True)
class Quota:
    """`count` 次 / `period_ms`，突发 `burst`。"""

    count: int
    period_ms: int
    burst: int

    @property
    def emission_ms(self) -> int:
        """向上取整到毫秒，TAT 始终为整数毫秒。"""
        return math.ceil(self.period_ms / self.count)


def parse_quota(value: Any) -> Any:
    """`10/s,burst=20` 格式。"""
    if not isinstance(value, str):
        return value
    match = _RULE.fullmatch(value.replace(" ", ""))
    if match is None:
        raise ValueError("must look like 10/s,burst=20")
    count, unit, burst = match.groups()
    return Quota(int(count), _PERIOD_MS[unit], int(burst))


class RateLimitSettings(SettingsGroup):
    """私有默认规则的配额；共享规则（约定 §11.6）不可配置。"""

    default: Annotated[Quota, NoDecode, BeforeValidator(parse_quota)] = Field(
        default=Quota(10, 1000, 20), validation_alias="RATE_LIMIT_DEFAULT"
    )
