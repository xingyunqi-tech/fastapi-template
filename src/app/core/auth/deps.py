"""鉴权依赖：nestjs JwtStrategy、JwtRefreshStrategy、OptionalJwtAuthGuard、PermissionsGuard。"""

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Final

from fastapi import Depends, Request

from app.common.context import AppContext, Context
from app.common.exceptions import HttpError, forbidden, unauthorized
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


@dataclass(frozen=True, slots=True)
class JwtUser:
    id: int
    session_id: int
    role_id: int | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RefreshUser:
    session_id: int
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
        raise unauthorized()
    try:
        return Jwt.verify(token, secret)
    except InvalidTokenError, TokenExpiredError:
        raise unauthorized() from None


def is_status_allowed_for_auth(status_id: int | None) -> bool:
    return status_id in {StatusId.ACTIVE, StatusId.INACTIVE}


async def _touch(context: AppContext, session: DbSession, session_id: int) -> None:
    """在请求事务的 SAVEPOINT 中更新 lastActiveAt；失败只记日志。"""
    try:
        async with session.begin_nested():
            await SessionService(SessionRepository(session), context.cache).touch(session_id)
    except Exception as error:
        logger.warning("Failed to update lastActiveAt of session %s: %s", session_id, error)


async def _validate_access(
    payload: dict[str, Any], context: Context, session: DbSession
) -> JwtUser:
    user_id = payload.get("id")
    session_id = payload.get("sessionId")
    if not user_id or not session_id:
        raise unauthorized()
    key = session_auth_cache_key(session_id)
    cached = await context.cache.get(key)
    if cached:
        if not is_status_allowed_for_auth(cached.get("statusId")):
            raise unauthorized("User is not active")
        return JwtUser(int(user_id), int(session_id), cached.get("roleId"), payload)
    record = await SessionRepository(session).find_by_id(int(session_id))
    user = (
        await UserRepository(session).find_by_id(record.user_id)
        if record and record.user_id
        else None
    )
    if record is None or user is None or str(user.id) != str(user_id):
        raise unauthorized("Session expired")
    if not is_status_allowed_for_auth(user.status_id):
        raise unauthorized("User is not active")
    await _touch(context, session, record.id)
    await context.cache.set(
        key, {"statusId": user.status_id, "roleId": user.role_id}, JWT_USER_CACHE_TTL_MS
    )
    return JwtUser(int(user_id), int(session_id), user.role_id, payload)


async def current_user(request: Request, context: Context, session: DbSession) -> JwtUser:
    secret = context.settings.auth.secret.get_secret_value()
    return await _validate_access(_verify(_bearer(request), secret), context, session)


async def optional_user(request: Request, context: Context, session: DbSession) -> JwtUser | None:
    token = _bearer(request)
    if token is None:
        return None
    try:
        payload = Jwt.verify(token, context.settings.auth.secret.get_secret_value())
        return await _validate_access(payload, context, session)
    except InvalidTokenError, TokenExpiredError, HttpError:
        return None


async def refresh_user(request: Request, context: Context) -> RefreshUser:
    secret = context.settings.auth.refresh_secret.get_secret_value()
    payload = _verify(_bearer(request), secret)
    session_id = payload.get("sessionId")
    if not session_id:
        raise unauthorized()
    return RefreshUser(int(session_id), str(payload.get("hash")))


CurrentUser = Annotated[JwtUser, Depends(current_user)]
OptionalUser = Annotated[JwtUser | None, Depends(optional_user)]
CurrentRefreshUser = Annotated[RefreshUser, Depends(refresh_user)]


def permissions_guard(
    required: Sequence[str], *, any_of: bool = False
) -> Callable[..., Awaitable[None]]:
    """PermissionsGuard：未声明权限 403；缺少权限 403，message 为 nestjs 原文。"""

    async def guard(user: CurrentUser, context: Context, session: DbSession) -> None:
        if not required:
            raise forbidden("No permission declared for this route")
        if user.role_id is None:
            raise forbidden("User role not found")
        granted = await PermissionsService(session, context.cache).permissions_for_role(
            user.role_id
        )
        if any_of:
            if not any(p in granted for p in required):
                raise forbidden(f"Missing any of permissions: {', '.join(required)}")
            return
        missing = [p for p in required if p not in granted]
        if missing:
            raise forbidden(f"Missing permissions: {', '.join(missing)}")

    return guard


register_auth(
    {
        Auth.JWT: current_user,
        Auth.OPTIONAL_JWT: optional_user,
        Auth.REFRESH_JWT: refresh_user,
    },
    permissions_guard,
)
