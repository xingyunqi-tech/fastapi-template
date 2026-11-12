from typing import Annotated, Any

from fastapi import Request

from app.common.client import client_info
from app.common.context import AppContext, Context
from app.common.params import Body, IdParam
from app.common.responses import iso_timestamp
from app.common.routing import ApiRouter, Auth
from app.contract.schemas import (
    AuthChangePasswordDto,
    AuthConfirmEmailDto,
    AuthDeleteAccountDto,
    AuthEmailLoginDto,
    AuthForgotPasswordDto,
    AuthRegisterLoginDto,
    AuthResetPasswordDto,
    AuthUpdateDto,
    LoginResponseDto,
    RefreshResponseDto,
    SessionResponseDto,
    User,
)
from app.core.auth.deps import CurrentRefreshUser, CurrentUser
from app.core.auth.service import AuthService
from app.core.session.repository import SessionRepository
from app.core.session.service import SessionService
from app.core.users.router import build_users_service
from app.core.users.serializers import View, serialize_user
from app.core.users.service import user_input_from
from app.infrastructure.database.session import DbSession

router = ApiRouter(prefix="/v1/auth", tags=["Auth"])


def _service(context: AppContext, session: Any) -> AuthService:
    return AuthService(
        session,
        context.settings.auth,
        build_users_service(context, session),
        SessionService(SessionRepository(session), context.cache),
        context.passwords,
        context.mailer,
    )


@router.endpoint(
    "POST",
    "/email/login",
    operation_id="loginWithEmail",
    auth=Auth.PUBLIC,
    response_model=LoginResponseDto,
    summary="邮箱登录",
)
async def login(
    body: Annotated[AuthEmailLoginDto, Body(AuthEmailLoginDto)],
    request: Request,
    context: Context,
    session: DbSession,
) -> dict[str, Any]:
    client = client_info(request, context.settings.app.trust_proxy)
    tokens, user = await _service(context, session).validate_login(
        body.email, body.password, client
    )
    return {**tokens, "user": serialize_user(user, context.file_urls, view=View.ME)}


@router.endpoint(
    "POST",
    "/email/register",
    operation_id="registerWithEmail",
    auth=Auth.PUBLIC,
    summary="邮箱注册",
)
async def register(
    body: Annotated[AuthRegisterLoginDto, Body(AuthRegisterLoginDto)],
    context: Context,
    session: DbSession,
) -> None:
    await _service(context, session).register(user_input_from(body))


@router.endpoint(
    "POST", "/email/confirm", operation_id="confirmEmail", auth=Auth.PUBLIC, summary="确认邮箱"
)
async def confirm_email(
    body: Annotated[AuthConfirmEmailDto, Body(AuthConfirmEmailDto)],
    context: Context,
    session: DbSession,
) -> None:
    await _service(context, session).confirm_email(body.hash)


@router.endpoint(
    "POST",
    "/email/confirm/new",
    operation_id="confirmNewEmail",
    auth=Auth.PUBLIC,
    summary="确认新邮箱",
)
async def confirm_new_email(
    body: Annotated[AuthConfirmEmailDto, Body(AuthConfirmEmailDto)],
    context: Context,
    session: DbSession,
) -> None:
    await _service(context, session).confirm_new_email(body.hash)


@router.endpoint(
    "POST", "/forgot/password", operation_id="forgotPassword", auth=Auth.PUBLIC, summary="忘记密码"
)
async def forgot_password(
    body: Annotated[AuthForgotPasswordDto, Body(AuthForgotPasswordDto)],
    context: Context,
    session: DbSession,
) -> None:
    await _service(context, session).forgot_password(body.email)


@router.endpoint(
    "POST", "/reset/password", operation_id="resetPassword", auth=Auth.PUBLIC, summary="重置密码"
)
async def reset_password(
    body: Annotated[AuthResetPasswordDto, Body(AuthResetPasswordDto)],
    context: Context,
    session: DbSession,
) -> None:
    await _service(context, session).reset_password(body.hash, body.password)


@router.endpoint(
    "GET",
    "/me",
    operation_id="getCurrentUser",
    auth=Auth.JWT,
    response_model=User,
    summary="获取当前用户信息",
)
async def me(user: CurrentUser, context: Context, session: DbSession) -> dict[str, Any] | None:
    found = await _service(context, session).me(user)
    return serialize_user(found, context.file_urls, view=View.ME) if found else None


@router.endpoint(
    "POST",
    "/refresh",
    operation_id="refreshToken",
    auth=Auth.REFRESH_JWT,
    response_model=RefreshResponseDto,
    summary="刷新令牌",
)
async def refresh(user: CurrentRefreshUser, context: Context, session: DbSession) -> dict[str, Any]:
    return await _service(context, session).refresh(user)


@router.endpoint(
    "GET",
    "/sessions",
    operation_id="getMySessions",
    auth=Auth.JWT,
    response_model=list[SessionResponseDto],
    summary="当前用户的登录设备",
)
async def get_sessions(
    user: CurrentUser, context: Context, session: DbSession
) -> list[dict[str, Any]]:
    records = await _service(context, session).list_sessions(user)
    return [
        {
            "id": record.id,
            "userAgent": record.user_agent,
            "ip": record.ip,
            "createdAt": iso_timestamp(record.created_at),
            "lastActiveAt": iso_timestamp(record.last_active_at) if record.last_active_at else None,
            "current": str(record.id) == str(user.session_id),
        }
        for record in records
    ]


@router.endpoint(
    "DELETE",
    "/sessions/{id}",
    operation_id="revokeMySession",
    auth=Auth.JWT,
    summary="下线指定设备",
)
async def revoke_session(
    user: CurrentUser,
    id: IdParam,
    context: Context,
    session: DbSession,
) -> None:
    await _service(context, session).revoke_session(user, id)


@router.endpoint(
    "POST",
    "/sessions/revoke-others",
    operation_id="revokeOtherSessions",
    auth=Auth.JWT,
    summary="下线其他设备",
)
async def revoke_other_sessions(user: CurrentUser, context: Context, session: DbSession) -> None:
    await _service(context, session).revoke_other_sessions(user)


@router.endpoint("POST", "/logout", operation_id="logout", auth=Auth.JWT, summary="退出登录")
async def logout(user: CurrentUser, context: Context, session: DbSession) -> None:
    await _service(context, session).logout(user)


@router.endpoint(
    "PUT",
    "/me",
    operation_id="updateCurrentUser",
    auth=Auth.JWT,
    response_model=User,
    summary="更新当前用户信息",
)
async def update_me(
    user: CurrentUser,
    body: Annotated[AuthUpdateDto, Body(AuthUpdateDto)],
    context: Context,
    session: DbSession,
) -> dict[str, Any] | None:
    updated = await _service(context, session).update(
        user, user_input_from(body), body.old_password, body.sms_code
    )
    return serialize_user(updated, context.file_urls, view=View.ME) if updated else None


@router.endpoint(
    "DELETE", "/me", operation_id="deleteCurrentUser", auth=Auth.JWT, summary="删除当前用户账号"
)
async def delete_me(
    user: CurrentUser,
    body: Annotated[AuthDeleteAccountDto, Body(AuthDeleteAccountDto)],
    context: Context,
    session: DbSession,
) -> None:
    await _service(context, session).soft_delete(user, body.old_password, body.sms_code)


@router.endpoint(
    "PUT", "/password", operation_id="changePassword", auth=Auth.JWT, summary="修改密码"
)
async def change_password(
    user: CurrentUser,
    body: Annotated[AuthChangePasswordDto, Body(AuthChangePasswordDto)],
    context: Context,
    session: DbSession,
) -> None:
    await _service(context, session).change_password(user, body.old_password, body.new_password)
