"""鉴权依赖：Bearer access 令牌、吊销检查、权限守卫与近期登录要求。"""

import re
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Final

from fastapi import Depends, Request

from app.common.context import Context
from app.common.errors import AppError
from app.common.routing import Auth, register_auth
from app.security.jwks import JwksUnavailableError
from app.security.jwt import InvalidTokenError, Principal, TokenExpiredError
from app.security.rbac import permissions_for

_BEARER_RE: Final = re.compile(r"(\S+)\s+(\S+)")
INVALID_TOKEN_CHALLENGE: Final = 'Bearer error="invalid_token"'  # noqa: S105
RECENT_LOGIN_SECONDS: Final = 600


def unauthorized(*, token_present: bool = True) -> AppError:
    """携带了令牌时追加 `error="invalid_token"`（RFC 6750）。"""
    headers = {"WWW-Authenticate": INVALID_TOKEN_CHALLENGE} if token_present else None
    return AppError("common.unauthorized", headers=headers)


def bearer_token(authorization: str | None) -> str | None:
    match = _BEARER_RE.fullmatch((authorization or "").strip())
    if not match or match.group(1).lower() != "bearer":
        return None
    return match.group(2)


async def authenticate(request: Request, context: Context) -> Principal:
    token = bearer_token(request.headers.get("authorization"))
    if token is None:
        raise unauthorized(token_present=False)
    try:
        principal = await context.verifier.verify(token)
    except JwksUnavailableError:
        raise AppError("common.serviceUnavailable") from None
    except TokenExpiredError:
        raise AppError(
            "auth.tokenExpired", headers={"WWW-Authenticate": INVALID_TOKEN_CHALLENGE}
        ) from None
    except InvalidTokenError:
        raise unauthorized() from None
    if await context.revocations.is_revoked(principal.sid):
        raise unauthorized()
    request.state.principal = principal
    return principal


async def optional_principal(request: Request, context: Context) -> Principal | None:
    """令牌缺失、无效、过期或已吊销时按匿名处理。"""
    if bearer_token(request.headers.get("authorization")) is None:
        return None
    try:
        return await authenticate(request, context)
    except AppError as error:
        if error.status == 401:
            return None
        raise


CurrentPrincipal = Annotated[Principal, Depends(authenticate)]
OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]


async def recent_login(principal: CurrentPrincipal) -> Principal:
    """约定 §5.4：`auth_time` 距今超过 10 分钟时 403 `auth.reauthRequired`。"""
    if time.time() - principal.auth_time > RECENT_LOGIN_SECONDS:
        raise AppError("auth.reauthRequired")
    return principal


RecentPrincipal = Annotated[Principal, Depends(recent_login)]


def permissions_guard(
    required: Sequence[str], *, any_of: bool = False
) -> Callable[..., Awaitable[None]]:
    """未声明权限或缺少权限时 403 `common.forbidden`。"""

    async def guard(principal: CurrentPrincipal) -> None:
        granted = permissions_for(principal.roles)
        allowed = (
            any(code in granted for code in required)
            if any_of
            else all(code in granted for code in required)
        )
        if not required or not allowed:
            raise AppError("common.forbidden")

    return guard


register_auth({Auth.JWT: authenticate, Auth.OPTIONAL_JWT: optional_principal}, permissions_guard)
