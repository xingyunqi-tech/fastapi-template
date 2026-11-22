"""鉴权依赖：access / refresh 令牌、可选登录与权限守卫。"""

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Final
lazy from uuid import UUID

from fastapi import Depends, Request

from app.common.context import AppContext, Context
from app.common.errors import AppError
from app.common.routing import Auth, register_auth
from app.common.security import InvalidTokenError, Jwt, TokenExpiredError
from app.core.permissions.service import PermissionsService, StatusId
from app.core.session.repository import SessionRepository
from app.core.session.service import SessionService, session_auth_cache_key
from app.core.users.repository import UserRepository
from app.infrastructure.database.session import DbSession

JWT_USER_CACHE_TTL_MS: Final = 30_000
logger = logging.getLogger("app.auth")
_BEARER_RE: Final = re.compile(r"(\S+)\s+(\S+)")
INVALID_TOKEN_CHALLENGE: Final = 'Bearer error="invalid_token"'  # noqa: S105


def unauthorized(*, token_present: bool = True) -> AppError:
    """携带了令牌时追加 `error="invalid_token"`（RFC 6750）。"""
    headers = {"WWW-Authenticate": INVALID_TOKEN_CHALLENGE} if token_present else None
    return AppError("common.unauthorized", headers=headers)


@dataclass(frozen=True, slots=True)
class JwtUser:
    id: UUID
    session_id: UUID
    role_id: int | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RefreshUser:
    session_id: UUID
    hash: str


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    match = _BEARER_RE.match(header)
    if not match or match.group(1).lower() != "bearer":
        return None
    return match.group(2)


def _verify(token: str | None, secret: str) -> dict[str, Any]:
    if token is None:
        raise unauthorized(token_present=False)
    try:
        return Jwt.verify(token, secret)
    except TokenExpiredError:
        raise AppError(
            "auth.tokenExpired", headers={"WWW-Authenticate": INVALID_TOKEN_CHALLENGE}
        ) from None
    except InvalidTokenError:
        raise unauthorized() from None


def is_status_allowed_for_auth(status_id: int | None) -> bool:
    return status_id in {StatusId.ACTIVE, StatusId.INACTIVE}


async def _touch(context: AppContext, session: DbSession, session_id: UUID) -> None:
    """在请求事务的 SAVEPOINT 中更新 lastActiveAt；失败只记日志。"""
    try:
        async with session.begin_nested():
            await SessionService(SessionRepository(session), context.cache).touch(session_id)
    except Exception as error:
        logger.warning("Failed to update lastActiveAt of session %s: %s", session_id, error)


def _claim_uuid(payload: dict[str, Any], name: str) -> UUID:
    value = payload.get(name)
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            pass
    raise unauthorized()


async def _validate_access(
    payload: dict[str, Any], context: Context, session: DbSession
) -> JwtUser:
    user_id = _claim_uuid(payload, "id")
    session_id = _claim_uuid(payload, "sessionId")
    key = session_auth_cache_key(session_id)
    cached = await context.cache.get(key)
    if cached:
        if not is_status_allowed_for_auth(cached.get("statusId")):
            raise unauthorized()
        return JwtUser(user_id, session_id, cached.get("roleId"), payload)
    record = await SessionRepository(session).find_by_id(session_id)
    user = (
        await UserRepository(session).find_by_id(record.user_id)
        if record and record.user_id
        else None
    )
    if record is None or user is None or user.id != user_id:
        raise unauthorized()
    if not is_status_allowed_for_auth(user.status_id):
        raise unauthorized()
    await _touch(context, session, record.id)
    await context.cache.set(
        key, {"statusId": user.status_id, "roleId": user.role_id}, JWT_USER_CACHE_TTL_MS
    )
    return JwtUser(user_id, session_id, user.role_id, payload)


async def current_user(request: Request, context: Context, session: DbSession) -> JwtUser:
    secret = context.settings.auth.secret.get_secret_value()
    user = await _validate_access(_verify(_bearer(request), secret), context, session)
    request.state.user_id = user.id
    return user


async def optional_user(request: Request, context: Context, session: DbSession) -> JwtUser | None:
    token = _bearer(request)
    if token is None:
        return None
    try:
        payload = Jwt.verify(token, context.settings.auth.secret.get_secret_value())
        return await _validate_access(payload, context, session)
    except InvalidTokenError, TokenExpiredError, AppError:
        return None


async def refresh_user(request: Request, context: Context) -> RefreshUser:
    secret = context.settings.auth.refresh_secret.get_secret_value()
    payload = _verify(_bearer(request), secret)
    return RefreshUser(_claim_uuid(payload, "sessionId"), str(payload.get("hash")))


CurrentUser = Annotated[JwtUser, Depends(current_user)]
OptionalUser = Annotated[JwtUser | None, Depends(optional_user)]
CurrentRefreshUser = Annotated[RefreshUser, Depends(refresh_user)]


def permissions_guard(
    required: Sequence[str], *, any_of: bool = False
) -> Callable[..., Awaitable[None]]:
    """未声明权限或缺少权限时 403 `common.forbidden`。"""

    async def guard(user: CurrentUser, context: Context, session: DbSession) -> None:
        if not required:
            raise AppError("common.forbidden")
        if user.role_id is None:
            raise AppError("common.forbidden")
        granted = await PermissionsService(session, context.cache).permissions_for_role(
            user.role_id
        )
        if any_of:
            if not any(p in granted for p in required):
                raise AppError("common.forbidden")
            return
        missing = [p for p in required if p not in granted]
        if missing:
            raise AppError("common.forbidden")

    return guard


register_auth(
    {
        Auth.JWT: current_user,
        Auth.OPTIONAL_JWT: optional_user,
        Auth.REFRESH_JWT: refresh_user,
    },
    permissions_guard,
)
