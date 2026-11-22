from fastapi import Request

from app.common.client import client_info
from app.common.context import AppContext, Context
from app.common.routing import ApiRouter, Auth, Throttle
from app.identity.auth.schemas import (
    ForgotPasswordRequest,
    LoginResponse,
    LoginWithEmailRequest,
    RefreshTokenRequest,
    RegisterWithEmailRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
)
from app.identity.auth.service import AuthService
from app.identity.users.schemas import User
from app.infrastructure.database.session import DbSession
from app.security.deps import CurrentPrincipal
lazy from app.identity.sessions.service import Login

router = ApiRouter(prefix="/v1/auth", tags=["Auth"])


def login_response(login: Login, context: AppContext) -> LoginResponse:
    return LoginResponse(
        access_token=login.tokens.access_token,
        refresh_token=login.tokens.refresh_token,
        expires_in=login.tokens.expires_in,
        user=User.from_model(login.user, context.file_urls),
    )


@router.endpoint(
    "POST",
    "/email/register",
    operation_id="registerWithEmail",
    auth=Auth.PUBLIC,
    status=201,
    throttle=Throttle(limit=3, ttl=3_600_000),
    summary="邮箱注册",
)
async def register_with_email(
    body: RegisterWithEmailRequest, request: Request, context: Context, session: DbSession
) -> LoginResponse:
    client = client_info(request, context.settings.app.trust_proxy)
    return login_response(await AuthService(context, session).register(body, client), context)


@router.endpoint(
    "POST",
    "/email/login",
    operation_id="loginWithEmail",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=10, ttl=60_000),
    summary="邮箱登录",
)
async def login_with_email(
    body: LoginWithEmailRequest, request: Request, context: Context, session: DbSession
) -> LoginResponse:
    client = client_info(request, context.settings.app.trust_proxy)
    return login_response(await AuthService(context, session).login(body, client), context)


@router.endpoint(
    "POST", "/email/verify", operation_id="verifyEmail", auth=Auth.PUBLIC, summary="验证邮箱"
)
async def verify_email(body: VerifyEmailRequest, context: Context, session: DbSession) -> None:
    await AuthService(context, session).verify_email(body.token)


@router.endpoint(
    "POST",
    "/email/verify/resend",
    operation_id="resendVerificationEmail",
    auth=Auth.JWT,
    status=202,
    throttle=Throttle(limit=3, ttl=3_600_000),
    summary="重发验证邮件",
)
async def resend_verification_email(
    principal: CurrentPrincipal, context: Context, session: DbSession
) -> None:
    await AuthService(context, session).resend_verification(principal)


@router.endpoint(
    "POST",
    "/password/forgot",
    operation_id="forgotPassword",
    auth=Auth.PUBLIC,
    status=202,
    throttle=Throttle(limit=3, ttl=3_600_000),
    summary="找回密码",
)
async def forgot_password(
    body: ForgotPasswordRequest, context: Context, session: DbSession
) -> None:
    await AuthService(context, session).forgot_password(body.email)


@router.endpoint(
    "POST",
    "/password/reset",
    operation_id="resetPassword",
    auth=Auth.PUBLIC,
    summary="重置密码",
)
async def reset_password(body: ResetPasswordRequest, context: Context, session: DbSession) -> None:
    await AuthService(context, session).reset_password(body.token, body.password)


@router.endpoint(
    "POST",
    "/refresh",
    operation_id="refreshToken",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=10, ttl=60_000),
    summary="刷新令牌",
)
async def refresh_token(
    body: RefreshTokenRequest, context: Context, session: DbSession
) -> LoginResponse:
    return login_response(await AuthService(context, session).refresh(body.refresh_token), context)


@router.endpoint("POST", "/logout", operation_id="logout", auth=Auth.JWT, summary="退出登录")
async def logout(principal: CurrentPrincipal, context: Context, session: DbSession) -> None:
    await AuthService(context, session).logout(principal)
