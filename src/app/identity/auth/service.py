"""约定 §11.1：邮箱、短信、第三方登录、refresh 与登出。"""

from typing import TYPE_CHECKING

from app.common.errors import AppError
from app.identity.auth.models import TokenPurpose
from app.identity.auth.tokens import VerificationTokens, password_fingerprint
from app.identity.providers.accounts import ProviderAccounts
from app.identity.providers.registry import provider_client
from app.identity.providers.sms import SmsScene, sms_codes
from app.identity.sessions.service import Login, SessionService, can_sign_in, utcnow
from app.identity.users.models import UserStatus
from app.identity.users.repository import UserRepository
from app.infrastructure.database.session import after_commit
from app.infrastructure.mail import MailMessage
from app.infrastructure.throttler import account_subject
from app.infrastructure.throttler.rules import AUTH_LOGIN_ACCOUNT, AUTH_MAIL_ACCOUNT
from app.infrastructure.throttler.storage import Mode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.client import ClientInfo
    from app.common.context import AppContext
    from app.identity.auth.schemas import (
        LoginWithEmailRequest,
        LoginWithSmsRequest,
        RegisterWithEmailRequest,
    )
    from app.identity.users.models import UserModel
    from app.security.jwt import Principal


class AuthService:
    def __init__(self, context: AppContext, session: AsyncSession) -> None:
        self.context = context
        self.session = session
        self.users = UserRepository(session)
        self.sessions = SessionService(context, session)
        self.tokens = VerificationTokens(session)

    def _mail_after_commit(self, template: str, to: str, token: str) -> None:
        mailer = self.context.mailer
        message = MailMessage(template, to, {"token": token})

        async def send() -> None:
            await mailer.send(message)

        after_commit(self.session, send)

    async def _send_verification(self, user: UserModel) -> None:
        if user.email is None or user.email_verified_at is not None:
            return
        token = await self.tokens.issue(user.id, TokenPurpose.VERIFY_EMAIL)
        self._mail_after_commit("verify-email", user.email, token)

    async def register(self, body: RegisterWithEmailRequest, client: ClientInfo) -> Login:
        user = await self.users.create(
            email=body.email,
            phone=None,
            password_hash=await self.context.passwords.hash_async(body.password),
            display_name=body.display_name,
        )
        await self._send_verification(user)
        return await self.sessions.sign_in(user, client)

    async def login(self, body: LoginWithEmailRequest, client: ClientInfo) -> Login:
        """账号不存在或无密码时校验固定假哈希；凭据正确但被禁用时 403。"""
        limiter = self.context.rate_limiter
        account = account_subject(body.email)
        await limiter.check(AUTH_LOGIN_ACCOUNT, account, mode=Mode.PEEK)
        user = await self.users.find_by_email(body.email)
        result = await self.context.passwords.verify_async(
            body.password, user.password_hash if user is not None else None
        )
        if user is None or not result.valid:
            await limiter.record_failure(AUTH_LOGIN_ACCOUNT, account)
            raise AppError("auth.invalidCredentials")
        await limiter.reset(AUTH_LOGIN_ACCOUNT, account)
        if user.status != UserStatus.ACTIVE:
            raise AppError("auth.accountDisabled")
        if result.updated_hash is not None and user.password_hash is not None:
            await self.users.rehash_password(user, user.password_hash, result.updated_hash)
        return await self.sessions.sign_in(user, client)

    async def send_sms_code(self, phone: str, scene: SmsScene) -> None:
        await sms_codes(self.context).send(phone, scene)

    async def login_with_sms(self, body: LoginWithSmsRequest, client: ClientInfo) -> Login:
        """未注册的手机号自动注册；账号被禁用时 403。"""
        limiter = self.context.rate_limiter
        account = account_subject(body.phone)
        await limiter.check(AUTH_LOGIN_ACCOUNT, account, mode=Mode.PEEK)
        try:
            await sms_codes(self.context).verify(body.phone, SmsScene.LOGIN, body.code)
        except AppError as error:
            if error.error_key == "auth.verificationCodeInvalid":
                await limiter.record_failure(AUTH_LOGIN_ACCOUNT, account)
            raise
        await limiter.reset(AUTH_LOGIN_ACCOUNT, account)
        user = await self.users.find_by_phone(body.phone)
        if user is None:
            user = await self.users.create(
                email=None, phone=body.phone, password_hash=None, display_name=None
            )
        if not can_sign_in(user):
            raise AppError("auth.accountDisabled")
        return await self.sessions.sign_in(user, client)

    async def login_with_provider(self, provider: str, code: str, client: ClientInfo) -> Login:
        """未知或未启用的 provider 404；首次登录自动注册。"""
        provider_api = provider_client(self.context, provider)
        if provider_api is None:
            raise AppError("auth.providerDisabled")
        identity = await provider_api.exchange(code)
        user = await ProviderAccounts(self.session).resolve(provider_api.id, identity)
        if user is None:
            raise AppError("common.conflict")
        if not can_sign_in(user):
            raise AppError("auth.accountDisabled")
        return await self.sessions.sign_in(user, client)

    async def verify_email(self, token: str) -> None:
        row = await self.tokens.consume(token, TokenPurpose.VERIFY_EMAIL)
        user = await self.users.find_by_id(row.user_id) if row is not None else None
        if user is None:
            raise AppError("auth.verificationTokenInvalid")
        if user.email_verified_at is None:
            user.email_verified_at = utcnow()

    async def resend_verification(self, principal: Principal) -> None:
        user = await self.users.find_by_id(principal.sub)
        if user is not None:
            await self._send_verification(user)

    async def forgot_password(self, email: str) -> None:
        """邮箱不存在或账号被禁用时不发送，响应相同。"""
        await self.context.rate_limiter.check(AUTH_MAIL_ACCOUNT, account_subject(email))
        user = await self.users.find_by_email(email)
        if user is None or user.email is None or user.status != UserStatus.ACTIVE:
            return
        token = await self.tokens.issue(
            user.id,
            TokenPurpose.RESET_PASSWORD,
            fingerprint=password_fingerprint(user.password_hash),
        )
        self._mail_after_commit("reset-password", user.email, token)

    async def reset_password(self, token: str, password: str) -> None:
        """令牌签发后密码已改变时无效；成功后吊销该用户全部会话。"""
        row = await self.tokens.consume(token, TokenPurpose.RESET_PASSWORD)
        user = await self.users.find_by_id(row.user_id) if row is not None else None
        if (
            row is None
            or user is None
            or row.password_fingerprint != password_fingerprint(user.password_hash)
        ):
            raise AppError("auth.verificationTokenInvalid")
        user.password_hash = await self.context.passwords.hash_async(password)
        await self.sessions.revoke(user.id)

    async def refresh(self, refresh_token: str, ip_subject: str) -> Login:
        return await self.sessions.refresh(refresh_token, ip_subject)

    async def logout(self, principal: Principal) -> None:
        await self.sessions.revoke(principal.sub, only=[principal.sid])
