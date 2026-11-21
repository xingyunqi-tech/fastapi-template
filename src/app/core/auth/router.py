from typing import Annotated, Any

from fastapi import Path, Request

from app.common.client import client_info
from app.common.context import AppContext, Context
from app.common.errors import AppError
from app.common.routing import ApiRouter, Auth, Throttle
from app.common.types import SerialId
from app.core.auth.deps import CurrentRefreshUser, CurrentUser
from app.core.auth.schemas import (
    AlipayLoginRequest,
    ChangePasswordRequest,
    ConfirmEmailRequest,
    DeleteAccountRequest,
    EmailLoginRequest,
    EmailRegisterRequest,
    ForgotPasswordRequest,
    LoginData,
    PhoneLoginRequest,
    PhoneRegisterRequest,
    PhoneResetPasswordRequest,
    PhoneSmsLoginRequest,
    ResetPasswordRequest,
    SendSmsCodeRequest,
    Session,
    TokenPair,
    UpdateMeRequest,
    WechatLoginRequest,
    WechatPhoneLoginRequest,
)
from app.core.auth.service import AuthService, LoginResult
from app.core.session.repository import SessionRepository
from app.core.session.service import SessionService
from app.core.users.router import build_users_service
from app.core.users.schemas import User
from app.core.users.service import user_input_from
from app.infrastructure.database.session import DbSession
from app.integrations.alipay import AlipayService
from app.integrations.sms import AliyunSmsProvider, SmsCodeType, SmsService
from app.integrations.wechat import WechatService

router = ApiRouter(prefix="/v1/auth", tags=["Auth"])


def _service(context: AppContext, session: Any) -> AuthService:
    transport = context.http_transport
    settings = context.settings
    return AuthService(
        session,
        settings.auth,
        build_users_service(context, session),
        SessionService(SessionRepository(session), context.cache),
        context.passwords,
        context.mailer,
        SmsService(
            settings.sms, context.cache, context.locks, AliyunSmsProvider(settings.sms, transport)
        ),
        WechatService(settings.wechat, context.cache, transport),
        AlipayService(settings.alipay, transport),
    )


def _login_response(result: LoginResult, context: AppContext) -> LoginData:
    return LoginData(
        **TokenPair.model_validate(result.tokens).model_dump(),
        user=User.from_model(result.user, context.file_urls),
    )


@router.endpoint(
    "POST",
    "/email/login",
    operation_id="loginWithEmail",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=5, ttl=60_000),
    summary="邮箱登录",
)
async def login(
    body: EmailLoginRequest, request: Request, context: Context, session: DbSession
) -> LoginData:
    client = client_info(request, context.settings.app.trust_proxy)
    tokens, user = await _service(context, session).validate_login(
        body.email, body.password, client
    )
    return _login_response(LoginResult(tokens, user), context)


@router.endpoint(
    "POST",
    "/email/register",
    operation_id="registerWithEmail",
    auth=Auth.PUBLIC,
    summary="邮箱注册",
)
async def register(body: EmailRegisterRequest, context: Context, session: DbSession) -> None:
    await _service(context, session).register(user_input_from(body))


@router.endpoint(
    "POST", "/email/confirm", operation_id="confirmEmail", auth=Auth.PUBLIC, summary="确认邮箱"
)
async def confirm_email(body: ConfirmEmailRequest, context: Context, session: DbSession) -> None:
    await _service(context, session).confirm_email(body.hash)


@router.endpoint(
    "POST",
    "/email/confirm/new",
    operation_id="confirmNewEmail",
    auth=Auth.PUBLIC,
    summary="确认新邮箱",
)
async def confirm_new_email(
    body: ConfirmEmailRequest, context: Context, session: DbSession
) -> None:
    await _service(context, session).confirm_new_email(body.hash)


@router.endpoint(
    "POST",
    "/forgot/password",
    operation_id="forgotPassword",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=3, ttl=300_000),
    summary="忘记密码",
)
async def forgot_password(
    body: ForgotPasswordRequest, context: Context, session: DbSession
) -> None:
    await _service(context, session).forgot_password(body.email)


@router.endpoint(
    "POST",
    "/reset/password",
    operation_id="resetPassword",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=5, ttl=300_000),
    summary="重置密码",
)
async def reset_password(body: ResetPasswordRequest, context: Context, session: DbSession) -> None:
    await _service(context, session).reset_password(body.hash, body.password)


@router.endpoint(
    "GET", "/me", operation_id="getCurrentUser", auth=Auth.JWT, summary="获取当前用户信息"
)
async def me(user: CurrentUser, context: Context, session: DbSession) -> User:
    found = await _service(context, session).me(user)
    if found is None:
        raise AppError("user.notFound")
    return User.from_model(found, context.file_urls)


@router.endpoint(
    "POST", "/refresh", operation_id="refreshToken", auth=Auth.REFRESH_JWT, summary="刷新令牌"
)
async def refresh(user: CurrentRefreshUser, context: Context, session: DbSession) -> TokenPair:
    return TokenPair.model_validate(await _service(context, session).refresh(user))


@router.endpoint(
    "GET",
    "/sessions",
    operation_id="getMySessions",
    auth=Auth.JWT,
    summary="当前用户的登录设备",
)
async def get_sessions(user: CurrentUser, context: Context, session: DbSession) -> list[Session]:
    records = await _service(context, session).list_sessions(user)
    return [
        Session(
            id=record.id,
            user_agent=record.user_agent,
            ip=record.ip,
            created_at=record.created_at,
            last_active_at=record.last_active_at,
            current=record.id == user.session_id,
        )
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
    user: CurrentUser, id: Annotated[SerialId, Path()], context: Context, session: DbSession
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
    throttle=Throttle(limit=10, ttl=60_000),
    summary="更新当前用户信息",
)
async def update_me(
    user: CurrentUser, body: UpdateMeRequest, context: Context, session: DbSession
) -> User:
    updated = await _service(context, session).update(
        user, user_input_from(body), body.old_password, body.sms_code
    )
    if updated is None:
        raise AppError("user.notFound")
    return User.from_model(updated, context.file_urls)


@router.endpoint(
    "DELETE", "/me", operation_id="deleteCurrentUser", auth=Auth.JWT, summary="删除当前用户账号"
)
async def delete_me(
    user: CurrentUser, body: DeleteAccountRequest, context: Context, session: DbSession
) -> None:
    await _service(context, session).soft_delete(user, body.old_password, body.sms_code)


@router.endpoint(
    "POST",
    "/phone/send-code",
    operation_id="sendSmsCode",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=3, ttl=60_000),
    summary="发送短信验证码",
)
async def send_sms_code(body: SendSmsCodeRequest, context: Context, session: DbSession) -> None:
    await _service(context, session).send_code(body.phone, body.type or SmsCodeType.LOGIN)


@router.endpoint(
    "POST",
    "/phone/login",
    operation_id="loginWithPhone",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=5, ttl=60_000),
    summary="手机号密码登录",
)
async def phone_login(
    body: PhoneLoginRequest, request: Request, context: Context, session: DbSession
) -> LoginData:
    client = client_info(request, context.settings.app.trust_proxy)
    result = await _service(context, session).validate_phone_login(
        body.phone, body.password, client
    )
    return _login_response(result, context)


@router.endpoint(
    "POST",
    "/phone/sms-login",
    operation_id="loginWithSms",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=5, ttl=60_000),
    summary="短信验证码登录",
)
async def phone_sms_login(
    body: PhoneSmsLoginRequest, request: Request, context: Context, session: DbSession
) -> LoginData:
    client = client_info(request, context.settings.app.trust_proxy)
    result = await _service(context, session).validate_phone_sms_login(
        body.phone, body.code, client
    )
    return _login_response(result, context)


@router.endpoint(
    "POST",
    "/phone/register",
    operation_id="registerWithPhone",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=3, ttl=60_000),
    summary="手机号注册",
)
async def phone_register(
    body: PhoneRegisterRequest, request: Request, context: Context, session: DbSession
) -> LoginData:
    client = client_info(request, context.settings.app.trust_proxy)
    result = await _service(context, session).register_by_phone(
        body.phone, body.code, body.password, body.nickname, client
    )
    return _login_response(result, context)


@router.endpoint(
    "POST",
    "/phone/reset-password",
    operation_id="resetPasswordByPhone",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=5, ttl=300_000),
    summary="通过手机验证码重置密码",
)
async def reset_password_by_phone(
    body: PhoneResetPasswordRequest, context: Context, session: DbSession
) -> None:
    await _service(context, session).reset_password_by_phone(body.phone, body.code, body.password)


@router.endpoint(
    "PUT",
    "/password",
    operation_id="changePassword",
    auth=Auth.JWT,
    throttle=Throttle(limit=3, ttl=60_000),
    summary="修改密码",
)
async def change_password(
    user: CurrentUser, body: ChangePasswordRequest, context: Context, session: DbSession
) -> None:
    await _service(context, session).change_password(user, body.old_password, body.new_password)


@router.endpoint(
    "POST",
    "/wechat/login",
    operation_id="loginWithWechat",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=10, ttl=60_000),
    summary="微信登录",
)
async def wechat_login(
    body: WechatLoginRequest, request: Request, context: Context, session: DbSession
) -> LoginData:
    client = client_info(request, context.settings.app.trust_proxy)
    result = await _service(context, session).wechat_login(
        body.code, body.type, body.nickname, client
    )
    return _login_response(result, context)


@router.endpoint(
    "POST",
    "/wechat/phone-login",
    operation_id="loginWithWechatPhone",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=10, ttl=60_000),
    summary="微信手机号快捷登录（小程序）",
)
async def wechat_phone_login(
    body: WechatPhoneLoginRequest, request: Request, context: Context, session: DbSession
) -> LoginData:
    client = client_info(request, context.settings.app.trust_proxy)
    result = await _service(context, session).wechat_phone_login(
        body.login_code, body.phone_code, client
    )
    return _login_response(result, context)


@router.endpoint(
    "POST",
    "/alipay/login",
    operation_id="loginWithAlipay",
    auth=Auth.PUBLIC,
    throttle=Throttle(limit=10, ttl=60_000),
    summary="支付宝登录（小程序）",
)
async def alipay_login(
    body: AlipayLoginRequest, request: Request, context: Context, session: DbSession
) -> LoginData:
    client = client_info(request, context.settings.app.trust_proxy)
    result = await _service(context, session).alipay_login(body.auth_code, client)
    return _login_response(result, context)
