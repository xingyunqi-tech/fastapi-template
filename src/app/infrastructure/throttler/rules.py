"""约定 §11.6 的共享规则（三个服务参数相同、共同计数）与本服务私有规则。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.config import Quota

MINUTE_MS: Final = 60_000
HOUR_MS: Final = 3_600_000


class SubjectKind(StrEnum):
    IP = "ip"
    USER = "user"
    CALLER = "caller"
    """已认证时按用户，否则按 IP。"""


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    quota: Quota
    subject: SubjectKind = SubjectKind.IP
    shared: bool = False
    failures_only: bool = False
    """只对失败计数，成功后清除该主体的键（约定 §11.6）。"""


AUTH_LOGIN: Final = Rule("auth-login", Quota(20, MINUTE_MS, 10), shared=True)
AUTH_LOGIN_ACCOUNT: Final = Rule(
    "auth-login-account", Quota(10, HOUR_MS, 5), shared=True, failures_only=True
)
AUTH_REGISTER: Final = Rule("auth-register", Quota(10, HOUR_MS, 3), shared=True)
AUTH_MAIL_USER: Final = Rule(
    "auth-mail", Quota(5, HOUR_MS, 3), subject=SubjectKind.USER, shared=True
)
AUTH_MAIL_IP: Final = Rule("auth-mail", Quota(5, HOUR_MS, 3), shared=True)
AUTH_SMS: Final = Rule("auth-sms", Quota(10, HOUR_MS, 5), shared=True)
AUTH_MAIL_ACCOUNT: Final = Rule("auth-mail-account", Quota(3, HOUR_MS, 2), shared=True)
AUTH_PASSWORD_VERIFY: Final = Rule(
    "auth-password-verify",
    Quota(5, 15 * MINUTE_MS, 5),
    subject=SubjectKind.USER,
    shared=True,
    failures_only=True,
)
AUTH_REFRESH: Final = Rule("auth-refresh", Quota(30, MINUTE_MS, 10), shared=True)

SHARED_RULES: Final = (
    AUTH_LOGIN,
    AUTH_LOGIN_ACCOUNT,
    AUTH_REGISTER,
    AUTH_MAIL_USER,
    AUTH_SMS,
    AUTH_MAIL_ACCOUNT,
    AUTH_PASSWORD_VERIFY,
    AUTH_REFRESH,
)


def private_rule(
    service: str, name: str, quota: Quota, subject: SubjectKind = SubjectKind.CALLER
) -> Rule:
    """私有规则名为 `{OTEL_SERVICE_NAME}-{name}`。"""
    return Rule(f"{service}-{name}", quota, subject=subject)


def default_rule(service: str, quota: Quota) -> Rule:
    return private_rule(service, "default", quota)
