"""约定 §11.1 邮箱部分、refresh 与登出。"""

from typing import TYPE_CHECKING

from app.common.errors import AppError
from app.identity.auth.models import TokenPurpose
from app.identity.auth.tokens import VerificationTokens, password_fingerprint
from app.identity.sessions.service import Login, SessionService, utcnow
from app.identity.users.models import UserStatus
from app.identity.users.repository import UserRepository
from app.infrastructure.database.session import after_commit
from app.infrastructure.mail import MailMessage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.client import ClientInfo
    from app.common.context import AppContext
    from app.identity.auth.schemas import LoginWithEmailRequest, RegisterWithEmailRequest
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
        user = await self.users.find_by_email(body.email)
        result = await self.context.passwords.verify_async(
            body.password, user.password_hash if user is not None else None
        )
        if user is None or not result.valid:
            raise AppError("auth.invalidCredentials")
        if user.status != UserStatus.ACTIVE:
            raise AppError("auth.accountDisabled")
        if result.updated_hash is not None and user.password_hash is not None:
            await self.users.rehash_password(user, user.password_hash, result.updated_hash)
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

    async def refresh(self, refresh_token: str) -> Login:
        return await self.sessions.refresh(refresh_token)

    async def logout(self, principal: Principal) -> None:
        await self.sessions.revoke(principal.sub, only=[principal.sid])
