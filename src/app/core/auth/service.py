"""各登录方式、邮箱确认、找回密码、令牌刷新、个人资料、改密、注销与会话管理。"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import (
    HttpError,
    find_unique_violation,
    not_found_error,
    unauthorized,
    unprocessable,
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
from app.integrations.sms import SmsCodeType, mask_phone
from app.modules.notifications.settings import initialize_default_settings
lazy from app.common.client import ClientInfo
lazy from app.config import AuthSettings
lazy from app.core.session.models import SessionModel
lazy from app.core.session.service import SessionService
lazy from app.core.users.serializers import CreatedRelations
lazy from app.integrations.alipay import AlipayService
lazy from app.integrations.sms import SmsService
lazy from app.integrations.wechat import WechatPhoneInfo, WechatService, WechatUserInfo

logger = logging.getLogger("app.auth")


def _role_payload(user: UserModel) -> dict[str, Any] | None:
    if user.role is None:
        return None
    return {"id": user.role.id, "name": user.role.name, "__entity": "RoleEntity"}


def _ensure_status_for_auth(user: UserModel, field: str) -> None:
    """nestjs validateUserStatusForAuth。"""
    if not is_status_allowed_for_auth(user.status_id):
        logger.warning(
            "Auth failed - user status not allowed: %s, status: %s", user.id, user.status_id
        )
        raise validation_error(field, "userNotActive")


def _phone_bound_to_other_wechat() -> HttpError:
    return unprocessable(
        {
            "status": 422,
            "error": "phoneBoundToOtherWechat",
            "errors": {"phone": "boundToOtherWechat"},
        }
    )


@dataclass(frozen=True, slots=True)
class LoginResult:
    """登录结果；新建的用户带 `created`，响应中的关联只含 id（同 nestjs save() 返回值）。"""

    tokens: dict[str, Any]
    user: UserModel
    created: CreatedRelations | None = None


@dataclass(frozen=True, slots=True)
class _NewUser:
    user: UserModel
    created: CreatedRelations


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        settings: AuthSettings,
        users: UsersService,
        sessions: SessionService,
        passwords: Passwords,
        mailer: Mailer,
        sms: SmsService,
        wechat: WechatService,
        alipay: AlipayService,
    ) -> None:
        self.session = session
        self.settings = settings
        self.users = users
        self.sessions = sessions
        self.passwords = passwords
        self.mailer = mailer
        self.sms = sms
        self.wechat = wechat
        self.alipay = alipay

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
        await self._verify_sms_code(user.phone, sms_code, SmsCodeType.VERIFY_IDENTITY)

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

    async def _verify_sms_code(self, phone: str, code: str, type_: SmsCodeType) -> None:
        result = await self.sms.verify_code(phone, code, type_)
        if not result.success:
            logger.warning("SMS verification failed for: %s", mask_phone(phone))
            raise validation_error("code", "invalidCode")

    async def _login(
        self, user: UserModel, client: ClientInfo, created: CreatedRelations | None = None
    ) -> LoginResult:
        hash_ = generate_session_hash()
        record = await self.sessions.create(
            user_id=user.id, hash_=hash_, user_agent=client.user_agent, ip=client.ip
        )
        role = (
            ({"id": created.role_id} if created.role_id is not None else None)
            if created is not None
            else _role_payload(user)
        )
        tokens = self._tokens(user_id=user.id, role=role, session_id=record.id, hash_=hash_)
        return LoginResult(tokens, user, created)

    async def _create_user(self, data: UserInput) -> _NewUser:
        """在保存点内创建用户并初始化通知设置，通知设置失败不影响创建。"""
        async with self.session.begin_nested():
            user, created = await self.users.create(data)
        try:
            async with self.session.begin_nested():
                await initialize_default_settings(self.session, user.id)
        except Exception:
            logger.warning("Failed to initialize notification settings for user %s", user.id)
        return _NewUser(user, created)

    async def send_code(self, phone: str, type_: SmsCodeType) -> None:
        logger.info("Send SMS code request for phone: %s, type: %s", mask_phone(phone), type_)
        if type_ is SmsCodeType.CHANGE_PHONE:
            raise validation_error("type", "unsupportedCodeType")
        if type_ is SmsCodeType.REGISTER and await self.users.find_by_phone(phone):
            raise validation_error("phone", "phoneAlreadyExists")
        if type_ in {
            SmsCodeType.LOGIN,
            SmsCodeType.RESET_PASSWORD,
            SmsCodeType.VERIFY_IDENTITY,
        } and not await self.users.find_by_phone(phone):
            raise validation_error("phone", "phoneNotFound")
        result = await self.sms.send_code(phone, type_)
        if not result.success:
            logger.warning("Failed to send SMS code to %s: %s", mask_phone(phone), result.message)
            errors: dict[str, Any] = {
                "phone": "rateLimited" if result.retry_after else "sendFailed"
            }
            if result.retry_after is not None:
                errors["retryAfter"] = result.retry_after
            raise unprocessable({"status": 422, "errors": errors})

    async def validate_phone_login(
        self, phone: str, password: str, client: ClientInfo
    ) -> LoginResult:
        user = await self.users.find_by_phone(phone)
        if user is None or not user.password or not await self._verify_password(user, password):
            raise validation_error("credentials", "incorrectPhoneOrPassword")
        _ensure_status_for_auth(user, "phone")
        return await self._login(user, client)

    async def validate_phone_sms_login(
        self, phone: str, code: str, client: ClientInfo
    ) -> LoginResult:
        await self._verify_sms_code(phone, code, SmsCodeType.LOGIN)
        user = await self.users.find_by_phone(phone)
        if user is None:
            raise validation_error("phone", "notFound")
        _ensure_status_for_auth(user, "phone")
        return await self._login(user, client)

    async def register_by_phone(
        self, phone: str, code: str, password: str, nickname: Any, client: ClientInfo
    ) -> LoginResult:
        await self._verify_sms_code(phone, code, SmsCodeType.REGISTER)
        if await self.users.find_by_phone(phone):
            raise validation_error("phone", "phoneAlreadyExists")
        new = await self._create_user(
            UserInput(
                phone=phone,
                password=password,
                nickname=nickname,
                provider="phone",
                role={"id": RoleId.USER},
                status={"id": StatusId.ACTIVE},
            )
        )
        return await self._login(new.user, client, new.created)

    async def reset_password_by_phone(self, phone: str, code: str, password: str) -> None:
        result = await self.sms.verify_code(phone, code, SmsCodeType.RESET_PASSWORD)
        if not result.success:
            raise validation_error("code", "invalidCode")
        user = await self.users.find_by_phone(phone)
        if user is None:
            raise validation_error("phone", "phoneNotExists")
        _ensure_status_for_auth(user, "phone")
        await self.session.execute(
            update(UserModel)
            .where(UserModel.id == user.id)
            .values(password=await self.passwords.hash_async(password))
        )
        await self.sessions.delete_by_user(user.id)

    async def _create_wechat_user(
        self,
        *,
        open_id: str | None,
        union_id: str | None,
        phone: str | None = None,
        nickname: str | None = None,
        gender: int | None = None,
    ) -> _NewUser:
        return await self._create_user(
            UserInput(
                wechat_open_id=open_id,
                wechat_union_id=union_id,
                phone=phone,
                nickname=nickname if nickname is not None else "微信用户",
                gender=gender if gender is not None else 0,
                provider="wechat",
                role={"id": RoleId.USER},
                status={"id": StatusId.ACTIVE},
            )
        )

    async def wechat_login(
        self, code: str, type_: str, nickname: str | None, client: ClientInfo
    ) -> LoginResult:
        info: WechatUserInfo = (
            await self.wechat.mini_app_login(code)
            if type_ == "mini_app"
            else await self.wechat.app_login(code)
        )
        user = await self.users.find_by_wechat_open_id(info.open_id)
        if user is not None:
            _ensure_status_for_auth(user, "wechat")
            return await self._login(user, client)
        try:
            new = await self._create_wechat_user(
                open_id=info.open_id,
                union_id=info.union_id,
                nickname=nickname if nickname is not None else info.nickname,
                gender=info.gender,
            )
        except Exception as error:
            violation = find_unique_violation(error)
            if violation is None or violation.constraint != "user_wechatOpenId_key":
                raise
            existing = await self.users.find_by_wechat_open_id(info.open_id)
            if existing is None:
                raise
            _ensure_status_for_auth(existing, "wechat")
            return await self._login(existing, client)
        return await self._login(new.user, client, new.created)

    async def wechat_phone_login(
        self, login_code: str, phone_code: str, client: ClientInfo
    ) -> LoginResult:
        results = await asyncio.gather(
            self.wechat.mini_app_login(login_code),
            self.wechat.get_phone_number(phone_code),
            return_exceptions=True,
        )
        for outcome in results:
            if isinstance(outcome, BaseException):
                raise outcome
        info: WechatUserInfo = results[0]  # type: ignore[assignment]
        phone_info: WechatPhoneInfo = results[1]  # type: ignore[assignment]
        if phone_info.country_code != "86":
            logger.warning(
                "WeChat phone login rejected - unsupported country code: %s",
                phone_info.country_code,
            )
            raise unprocessable(
                {
                    "status": 422,
                    "error": "phoneRegionNotSupported",
                    "errors": {"phone": "unsupportedRegion"},
                }
            )
        phone = phone_info.pure_phone_number
        found = await self._resolve_wechat_phone_user(info, phone, allow_retry=True)
        if found is None:
            found = await self._resolve_wechat_phone_user(info, phone, allow_retry=False)
        if found is None:
            raise validation_error("user", "notFound")
        user = found.user if isinstance(found, _NewUser) else found
        _ensure_status_for_auth(user, "wechat")
        return await self._login(
            user, client, found.created if isinstance(found, _NewUser) else None
        )

    async def _resolve_wechat_phone_user(
        self, info: WechatUserInfo, phone: str, *, allow_retry: bool
    ) -> UserModel | _NewUser | None:
        """按 openId 与手机号查找、绑定或创建用户；allow_retry 时并发冲突返回 None。"""
        by_open_id = await self.users.find_by_wechat_open_id(info.open_id)
        by_phone = await self.users.find_by_phone(phone)

        if by_open_id is not None and by_phone is not None:
            if by_open_id.id != by_phone.id:
                logger.warning("WeChat phone login rejected - phone bound: %s", mask_phone(phone))
                raise _phone_bound_to_other_wechat()
            return by_open_id

        if by_open_id is not None:
            if by_open_id.phone:
                return by_open_id
            _ensure_status_for_auth(by_open_id, "wechat")
            user_id = by_open_id.id
            try:
                async with self.session.begin_nested():
                    bound = await self.users.bind_phone_if_empty(user_id, phone)
                if not bound:
                    raise RuntimeError(f"Failed to bind phone to user {user_id}")
            except Exception:
                if allow_retry:
                    return None
                raise
            return await self.users.find_by_id(user_id)

        if by_phone is not None:
            if by_phone.wechat_open_id:
                logger.warning("WeChat phone login rejected - phone bound: %s", mask_phone(phone))
                raise _phone_bound_to_other_wechat()
            _ensure_status_for_auth(by_phone, "wechat")
            user_id = by_phone.id
            try:
                async with self.session.begin_nested():
                    bound = await self.users.bind_wechat_if_empty(
                        user_id, info.open_id, info.union_id
                    )
                if not bound:
                    raise RuntimeError(f"Failed to bind WeChat to user {user_id}")
            except Exception:
                if allow_retry:
                    return None
                raise
            return await self.users.find_by_id(user_id)

        try:
            return await self._create_wechat_user(
                open_id=info.open_id, union_id=info.union_id, phone=phone
            )
        except Exception:
            if allow_retry:
                return None
            raise

    async def alipay_login(self, auth_code: str, client: ClientInfo) -> LoginResult:
        open_id = await self.alipay.exchange_auth_code(auth_code)
        user = await self.users.find_by_alipay_open_id(open_id)
        if user is None:
            try:
                new = await self._create_user(
                    UserInput(
                        alipay_open_id=open_id,
                        nickname="支付宝用户",
                        provider="alipay",
                        role={"id": RoleId.USER},
                        status={"id": StatusId.ACTIVE},
                    )
                )
            except Exception as error:
                violation = find_unique_violation(error)
                if violation is None or violation.constraint != "user_alipayOpenId_key":
                    raise
                user = await self.users.find_by_alipay_open_id(open_id)
                if user is None:
                    raise
            else:
                return await self._login(new.user, client, new.created)
        _ensure_status_for_auth(user, "alipay")
        return await self._login(user, client)
