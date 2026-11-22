"""第三方登录 provider 协议与错误映射。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from app.common.errors import AppError


class ProviderId(StrEnum):
    WECHAT_MINIAPP = "wechat_miniapp"
    WECHAT_APP = "wechat_app"
    ALIPAY = "alipay"


WECHAT_PROVIDERS: Final = frozenset({ProviderId.WECHAT_MINIAPP, ProviderId.WECHAT_APP})


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    openid: str
    union_id: str | None = None


class ProviderClient(Protocol):
    id: ProviderId

    async def exchange(self, code: str) -> ProviderIdentity:
        """`code` 无效时 422 `auth.providerCodeInvalid`，调用失败时 502 `common.badGateway`。"""
        ...


def code_invalid() -> AppError:
    return AppError("auth.providerCodeInvalid")


def bad_gateway() -> AppError:
    return AppError("common.badGateway")
