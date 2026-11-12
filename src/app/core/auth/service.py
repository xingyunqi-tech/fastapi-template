"""邮箱登录注册、邮箱确认、找回密码、令牌刷新、个人资料、改密、注销与会话管理。"""

import logging
from typing import Any

from sqlalchemy import update
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import (
    not_found_error,
    unauthorized,
    validation_error,
)
from app.common.security import (
    InvalidTokenError,
    Jwt,
    Passwords,
    TokenExpiredError,
    generate_session_hash,
    ms,
    now_ms,
    password_fingerprint,
)
from app.core.auth.deps import JwtUser, RefreshUser, is_status_allowed_for_auth
from app.core.permissions.service import RoleId, StatusId
from app.core.users.models import UserModel
from app.core.users.repository import UNSET
from app.core.users.service import UserInput, UsersService
from app.infrastructure.database.session import affected_rows, after_commit
from app.integrations.mail import Mailer, MailMessage
from app.modules.notifications.settings import initialize_default_settings
lazy from app.common.client import ClientInfo
lazy from app.config import AuthSettings
lazy from app.core.session.models import SessionModel
lazy from app.core.session.service import SessionService

logger = logging.getLogger("app.auth")


def _role_payload(user: UserModel) -> dict[str, Any] | None:
    if user.role is None:
        return None
    return {"id": user.role.id, "name": user.role.name, "__entity": "RoleEntity"}


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        settings: AuthSettings,
        users: UsersService,
        sessions: SessionService,
        passwords: Passwords,
        mailer: Mailer,
    ) -> None:
        self.session = session
        self.settings = settings
        self.users = users
        self.sessions = sessions
        self.passwords = passwords
        self.mailer = mailer

    def _send_after_commit(self, message: MailMessage) -> None:
        async def send() -> None:
            await self.mailer.send(message)

        after_commit(self.session, send)

    async def _verify_password(self, user: UserModel, md5_password: str) -> bool:
        if not user.password:
            return False
        result = await self.passwords.verify_md5_async(md5_password, user.password)
        if result.valid and result.updated_hash:
            await self.session.execute(
                update(UserModel)
                .where(UserModel.id == user.id, UserModel.password == user.password)
                .values(password=result.updated_hash, updated_at=UserModel.updated_at)
            )
        return result.valid

    def _tokens(self, *, user_id: int, role: Any, session_id: int, hash_: str) -> dict[str, Any]:
        token = Jwt.sign(
            {"id": user_id, "role": role, "sessionId": session_id},
            self.settings.secret.get_secret_value(),
            self.settings.expires,
        )
        refresh = Jwt.sign(
            {"sessionId": session_id, "hash": hash_},
            self.settings.refresh_secret.get_secret_value(),
            self.settings.refresh_expires,
        )
        return {
            "refreshToken": refresh,
            "token": token,
            "tokenExpires": now_ms() + int(ms(self.settings.expires)),
        }

    async def create_session_and_tokens(
        self, user: UserModel, client: ClientInfo
    ) -> tuple[dict[str, Any], UserModel]:
        hash_ = generate_session_hash()
        record = await self.sessions.create(
            user_id=user.id, hash_=hash_, user_agent=client.user_agent, ip=client.ip
        )
        tokens = self._tokens(
            user_id=user.id, role=_role_payload(user), session_id=record.id, hash_=hash_
        )
        return tokens, user

    async def validate_login(
        self, email: str, password: str, client: ClientInfo
    ) -> tuple[dict[str, Any], UserModel]:
        user = await self.users.find_by_email(email)
        if user is None:
            raise validation_error("email", "notFound")
        if not user.password or not await self._verify_password(user, password):
            raise validation_error("password", "incorrectPassword")
        if not is_status_allowed_for_auth(user.status_id):
            raise validation_error("email", "userNotActive")
        return await self.create_session_and_tokens(user, client)

    async def register(self, data: UserInput) -> None:
        data.role = {"id": RoleId.USER}
        data.status = {"id": StatusId.INACTIVE}
        user, _ = await self.users.create(data)
        try:
            async with self.session.begin_nested():
                await initialize_default_settings(self.session, user.id)
        except Exception:
            logger.warning("Failed to initialize notification settings for user %s", user.id)
        hash_ = Jwt.sign(
            {"confirmEmailUserId": user.id},
            self.settings.confirm_email_secret.get_secret_value(),
            self.settings.confirm_email_expires,
        )
        self._send_after_commit(MailMessage("activation", str(data.email), {"hash": hash_}))

    def _decode(self, hash_: str, secret: str) -> dict[str, Any]:
        try:
            return Jwt.verify(hash_, secret)
        except TokenExpiredError:
            raise validation_error("hash", "tokenExpired") from None
        except InvalidTokenError:
            raise validation_error("hash", "invalidHash") from None

    async def confirm_email(self, hash_: str) -> None:
        payload = self._decode(hash_, self.settings.confirm_email_secret.get_secret_value())
        user = await self.users.find_by_id(int(payload.get("confirmEmailUserId") or 0))
        if user is None:
            raise not_found_error("notFound")
        if user.status_id != StatusId.INACTIVE:
            raise validation_error("hash", "alreadyConfirmed")
        await self.users.update(user.id, UserInput(status={"id": StatusId.ACTIVE}))

    async def confirm_new_email(self, hash_: str) -> None:
        payload = self._decode(hash_, self.settings.confirm_email_secret.get_secret_value())
        user = await self.users.find_by_id(int(payload.get("confirmEmailUserId") or 0))
        if user is None:
            raise not_found_error("notFound")
        data = UserInput(email=payload.get("newEmail"))
        if user.status_id == StatusId.INACTIVE:
            data.status = {"id": StatusId.ACTIVE}
        await self.users.update(user.id, data)

    async def forgot_password(self, email: str) -> None:
        user = await self.users.find_by_email(email)
        if user is None:
            raise validation_error("email", "emailNotExists")
        token_expires = now_ms() + int(ms(self.settings.forgot_expires))
        hash_ = Jwt.sign(
            {"forgotUserId": user.id, "pwd": password_fingerprint(user.password)},
            self.settings.forgot_secret.get_secret_value(),
            self.settings.forgot_expires,
        )
        self._send_after_commit(
            MailMessage("reset-password", email, {"hash": hash_, "tokenExpires": token_expires})
        )

    async def reset_password(self, hash_: str, password: str) -> None:
        payload = self._decode(hash_, self.settings.forgot_secret.get_secret_value())
        user = await self.users.find_by_id(int(payload.get("forgotUserId") or 0))
        if user is None:
            raise validation_error("hash", "notFound")
        if payload.get("pwd") != password_fingerprint(user.password):
            raise validation_error("hash", "invalidHash")
        result = await self.session.execute(
            update(UserModel)
            .where(
                UserModel.id == user.id,
                UserModel.deleted_at.is_(None),
                UserModel.password == user.password
                if user.password is not None
                else UserModel.password.is_(None),
            )
            .values(password=await self.passwords.hash_async(password))
        )
        if not affected_rows(result):
            raise validation_error("hash", "invalidHash")
        await self.sessions.delete_by_user(user.id)

    async def me(self, current: JwtUser) -> UserModel | None:
        return await self.users.find_by_id(current.id)

    async def _verify_identity(self, user: UserModel, old_password: Any, sms_code: Any) -> None:
        if user.password:
            if not old_password:
                raise validation_error("oldPassword", "missingOldPassword")
            if not await self._verify_password(user, old_password):
                raise validation_error("oldPassword", "incorrectOldPassword")
            return
        if not user.phone:
            raise validation_error("phone", "phoneRequiredForVerification")
        if not sms_code:
            raise validation_error("smsCode", "missingSmsCode")
        raise validation_error("code", "invalidCode")

    async def update(
        self, current: JwtUser, data: UserInput, old_password: Any, sms_code: Any
    ) -> UserModel | None:
        user = await self.users.find_by_id(current.id)
        if user is None:
            raise validation_error("user", "userNotFound")
        if data.password not in (UNSET, None) and data.password:
            if not old_password:
                raise validation_error("oldPassword", "missingOldPassword")
            if not user.password or not await self._verify_password(user, old_password):
                raise validation_error("oldPassword", "incorrectOldPassword")
            await self.sessions.delete_by_user(user.id, exclude=current.session_id)
        if data.email not in (UNSET, None) and data.email and data.email != user.email:
            existing = await self.users.find_by_email(data.email)
            if existing is not None and existing.id != user.id:
                raise validation_error("email", "emailExists")
            await self._verify_identity(user, old_password, sms_code)
            hash_ = Jwt.sign(
                {"confirmEmailUserId": user.id, "newEmail": data.email},
                self.settings.confirm_email_secret.get_secret_value(),
                self.settings.confirm_email_expires,
            )
            self._send_after_commit(MailMessage("confirm-new-email", data.email, {"hash": hash_}))
            data.email = UNSET
        await self.users.update(current.id, data)
        return await self.users.find_by_id(current.id)

    async def refresh(self, current: RefreshUser) -> dict[str, Any]:
        record: SessionModel | None = await self.sessions.find_by_id(current.session_id)
        owner = await self.users.find_by_id(record.user_id) if record and record.user_id else None
        if record is None or owner is None:
            raise unauthorized()
        if record.hash != current.hash:
            if not await self.sessions.was_recently_rotated_from(record.id, current.hash):
                await self.sessions.delete(record.id)
            raise unauthorized()
        if owner.role is None:
            raise unauthorized()
        if not is_status_allowed_for_auth(owner.status_id):
            raise validation_error("refresh", "userNotActive")
        hash_ = generate_session_hash()
        tokens = self._tokens(
            user_id=owner.id, role={"id": owner.role.id}, session_id=record.id, hash_=hash_
        )
        if not await self.sessions.rotate_hash(record.id, current.hash, hash_):
            raise unauthorized()
        return {
            "token": tokens["token"],
            "refreshToken": tokens["refreshToken"],
            "tokenExpires": tokens["tokenExpires"],
        }

    async def soft_delete(self, current: JwtUser, old_password: Any, sms_code: Any) -> None:
        user = await self.users.find_by_id(current.id)
        if user is None:
            raise validation_error("user", "userNotFound")
        await self._verify_identity(user, old_password, sms_code)
        await self.users.remove(current.id)

    async def change_password(self, current: JwtUser, old_password: str, new_password: str) -> None:
        user = await self.users.find_by_id(current.id)
        if user is None:
            raise validation_error("user", "userNotFound")
        if not user.password or not await self._verify_password(user, old_password):
            raise validation_error("oldPassword", "incorrectOldPassword")
        await self.users.update(current.id, UserInput(password=new_password))
        await self.sessions.delete_by_user(current.id, exclude=current.session_id)

    async def list_sessions(self, current: JwtUser) -> list[SessionModel]:
        return await self.sessions.find_active_by_user(current.id)

    async def revoke_session(self, current: JwtUser, session_id: int) -> None:
        if not await self.sessions.revoke_for_user(session_id, current.id):
            raise not_found_error("sessionNotFound")

    async def revoke_other_sessions(self, current: JwtUser) -> None:
        await self.sessions.delete_by_user(current.id, exclude=current.session_id)

    async def logout(self, current: JwtUser) -> None:
        await self.sessions.delete(current.session_id)
