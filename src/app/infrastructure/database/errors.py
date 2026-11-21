"""PostgreSQL 唯一约束冲突识别与对外错误映射。"""

from dataclasses import dataclass
from typing import Final
lazy from collections.abc import Mapping

PG_UNIQUE_VIOLATION: Final = "23505"

UNIQUE_CONSTRAINT_ERRORS: Final[Mapping[str, str]] = {
    "UQ_e12875dfb3b1d92d7d7c5377e22": "user.emailTaken",
    "user_phone_key": "user.phoneTaken",
}


@dataclass(frozen=True, slots=True)
class UniqueViolation:
    constraint: str | None

    @property
    def error_key(self) -> str:
        return UNIQUE_CONSTRAINT_ERRORS.get(self.constraint or "", "common.conflict")


def find_unique_violation(exc: BaseException) -> UniqueViolation | None:
    """沿 `orig` / `__cause__` 链查找 PostgreSQL 23505，返回链上首个可得的约束名。"""
    current: BaseException | None = exc
    found = False
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if code == PG_UNIQUE_VIOLATION:
            found = True
            constraint = getattr(current, "constraint_name", None)
            diag = getattr(current, "diag", None)
            if constraint is None and diag is not None:
                constraint = getattr(diag, "constraint_name", None)
            if constraint is not None:
                return UniqueViolation(constraint)
        orig = getattr(current, "orig", None)
        current = orig if isinstance(orig, BaseException) else current.__cause__
    return UniqueViolation(None) if found else None
