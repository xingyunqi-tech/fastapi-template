from fastapi import Request

from app.common.client import client_info
from app.common.context import AppContext, Context
from app.common.routing import ApiRouter, Auth
from app.identity.auth.schemas import (
    AuthProvider,
    ForgotPasswordRequest,
    LoginResponse,
    LoginWithEmailRequest,
    LoginWithProviderRequest,
    LoginWithSmsRequest,
    RefreshTokenRequest,
    RegisterWithEmailRequest,
    ResetPasswordRequest,
    SendSmsCodeRequest,
    VerifyEmailRequest,
)
from app.identity.auth.service import AuthService
from app.identity.providers.registry import enabled_providers
from app.identity.providers.sms import SmsScene
from app.identity.users.schemas import User
from app.infrastructure.database.session import DbSession
from app.infrastructure.throttler import ip_subject
from app.infrastructure.throttler.rules import (
    AUTH_LOGIN,
    AUTH_MAIL_IP,
    AUTH_MAIL_USER,
    AUTH_REGISTER,
    AUTH_SMS,
)
from app.security.deps import CurrentPrincipal, OptionalPrincipal, authenticate
lazy from app.identity.sessions.service import Login

router = ApiRouter(prefix="/v1/auth", tags=["Auth"])


def login_response(login: Login, context: AppContext) -> LoginResponse:
    return LoginResponse(
        access_token=login.tokens.access_token,
        refresh_token=login.tokens.refresh_token,
        expires_in=login.tokens.expires_in,
        user=User.from_model(login.user, context.urls),
    )


@router.endpoint(
    "POST",
    "/email/register",
    operation_id="registerWithEmail",
    auth=Auth.PUBLIC,
    status=201,
    rate_limit=[AUTH_REGISTER],
    summary="邮箱注册",
)
async def register_with_email(
    body: RegisterWithEmailRequest, request: Request, context: Context, session: DbSession
) -> LoginResponse:
    client = client_info(request, context.settings.app.trusted_proxies)
    return login_response(await AuthService(context, session).register(body, client), context)


@router.endpoint(
    "POST",
    "/email/login",
    operation_id="loginWithEmail",
    auth=Auth.PUBLIC,
    rate_limit=[AUTH_LOGIN],
    summary="邮箱登录",
)
async def login_with_email(
    body: LoginWithEmailRequest, request: Request, context: Context, session: DbSession
) -> LoginResponse:
    client = client_info(request, context.settings.app.trusted_proxies)
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
    rate_limit=[AUTH_MAIL_USER],
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
    rate_limit=[AUTH_MAIL_IP],
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
    "/sms/send",
    operation_id="sendSmsCode",
    auth=Auth.OPTIONAL_JWT,
    status=202,
    rate_limit=[AUTH_SMS],
    summary="发送短信验证码",
    description="`scene=login` 公开；`scene=bind_phone` 需要 JWT。",
)
async def send_sms_code(
    body: SendSmsCodeRequest,
    principal: OptionalPrincipal,
    request: Request,
    context: Context,
    session: DbSession,
) -> None:
    if body.scene is SmsScene.BIND_PHONE and principal is None:
        await authenticate(request, context)
    await AuthService(context, session).send_sms_code(body.phone, body.scene)


@router.endpoint(
    "POST",
    "/sms/login",
    operation_id="loginWithSms",
    auth=Auth.PUBLIC,
    rate_limit=[AUTH_LOGIN],
    summary="短信登录",
)
async def login_with_sms(
    body: LoginWithSmsRequest, request: Request, context: Context, session: DbSession
) -> LoginResponse:
    client = client_info(request, context.settings.app.trusted_proxies)
    return login_response(await AuthService(context, session).login_with_sms(body, client), context)


@router.endpoint(
    "GET",
    "/providers",
    operation_id="listAuthProviders",
    auth=Auth.PUBLIC,
    summary="已启用的第三方登录",
)
async def list_auth_providers(context: Context) -> list[AuthProvider]:
    return [AuthProvider(id=provider.value) for provider in enabled_providers(context)]


@router.endpoint(
    "POST",
    "/providers/{provider}/login",
    operation_id="loginWithProvider",
    auth=Auth.PUBLIC,
    rate_limit=[AUTH_LOGIN],
    summary="第三方登录",
    description="`provider`：`wechat_miniapp`、`wechat_app`、`alipay`；未启用时 404。",
)
async def login_with_provider(
    provider: str,
    body: LoginWithProviderRequest,
    request: Request,
    context: Context,
    session: DbSession,
) -> LoginResponse:
    client = client_info(request, context.settings.app.trusted_proxies)
    login = await AuthService(context, session).login_with_provider(provider, body.code, client)
    return login_response(login, context)


@router.endpoint(
    "POST",
    "/refresh",
    operation_id="refreshToken",
    auth=Auth.PUBLIC,
    rate_limit=(),
    summary="刷新令牌",
)
async def refresh_token(
    body: RefreshTokenRequest, request: Request, context: Context, session: DbSession
) -> LoginResponse:
    ip = ip_subject(request, context.settings)
    return login_response(
        await AuthService(context, session).refresh(body.refresh_token, ip), context
    )


@router.endpoint("POST", "/logout", operation_id="logout", auth=Auth.JWT, summary="退出登录")
async def logout(principal: CurrentPrincipal, context: Context, session: DbSession) -> None:
    await AuthService(context, session).logout(principal)
