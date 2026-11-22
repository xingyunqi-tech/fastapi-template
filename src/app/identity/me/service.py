"""约定 §11.2 当前用户。"""

import time
from typing import TYPE_CHECKING, Final

from app.common.errors import AppError, FieldError, ValidationFailed, field_error
from app.common.types import normalize_phone
from app.identity.account_deletion.service import delete_account
from app.identity.providers import bad_gateway
from app.identity.providers.registry import miniapp_client
from app.identity.providers.sms import SmsScene, sms_codes
from app.identity.sessions.service import SessionService
from app.identity.users.repository import UserRepository
from app.modules.files.service import FilesService
from app.security.deps import RECENT_LOGIN_SECONDS

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.context import AppContext
    from app.identity.me.schemas import BindPhoneRequest, ChangePasswordRequest, UpdateMeRequest
    from app.identity.sessions.models import SessionModel
    from app.identity.users.models import UserModel
    from app.security.jwt import Principal

AVATAR_CONTENT_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/webp"})


class MeService:
    def __init__(self, context: AppContext, session: AsyncSession) -> None:
        self.context = context
        self.session = session
        self.users = UserRepository(session)
        self.sessions = SessionService(context, session)

    async def get(self, principal: Principal) -> UserModel:
        user = await self.users.find_by_id(principal.sub)
        if user is None:
            raise AppError("user.notFound")
        return user

    async def update(self, principal: Principal, body: UpdateMeRequest) -> UserModel:
        """`avatarFileId` 须为本人上传的图片，否则 422 `exists`。"""
        user = await self.get(principal)
        fields = body.model_fields_set
        if "avatar_file_id" in fields and body.avatar_file_id is not None:
            file = await FilesService(self.session).find_by_id(body.avatar_file_id)
            if (
                file is None
                or file.owner_user_id != user.id
                or file.mime_type not in AVATAR_CONTENT_TYPES
            ):
                raise field_error("avatarFileId", "exists")
        if "display_name" in fields:
            user.display_name = body.display_name
        if "avatar_file_id" in fields:
            user.avatar_file_id = body.avatar_file_id
        return await self.users.reload(user)

    async def delete(self, principal: Principal) -> None:
        await delete_account(self.context, self.session, await self.get(principal))

    async def change_password(self, principal: Principal, body: ChangePasswordRequest) -> None:
        """有密码时校验 `currentPassword`；无密码时要求近期登录。成功后吊销其他会话。"""
        user = await self.get(principal)
        if user.password_hash is not None:
            if body.current_password is None:
                raise ValidationFailed([FieldError("currentPassword", "required")])
            result = await self.context.passwords.verify_async(
                body.current_password, user.password_hash
            )
            if not result.valid:
                raise AppError("auth.passwordIncorrect")
        elif time.time() - principal.auth_time > RECENT_LOGIN_SECONDS:
            raise AppError("auth.reauthRequired")
        user.password_hash = await self.context.passwords.hash_async(body.new_password)
        await self.sessions.revoke(user.id, keep=principal.sid)

    async def _bind_phone(self, principal: Principal, phone: str) -> UserModel:
        """手机号属于其他账号时 409 `user.phoneTaken`。"""
        user = await self.get(principal)
        owner = await self.users.find_by_phone(phone)
        if owner is not None and owner.id != user.id:
            raise AppError("user.phoneTaken")
        user.phone = phone
        return await self.users.reload(user)

    async def bind_phone(self, principal: Principal, body: BindPhoneRequest) -> UserModel:
        await sms_codes(self.context).verify(body.phone, SmsScene.BIND_PHONE, body.code)
        return await self._bind_phone(principal, body.phone)

    async def bind_phone_with_wechat(self, principal: Principal, code: str) -> UserModel:
        """`wechat_miniapp` 未启用时 404 `auth.providerDisabled`。"""
        client = miniapp_client(self.context)
        if client is None:
            raise AppError("auth.providerDisabled")
        try:
            phone = normalize_phone(await client.phone_number(code))
        except ValueError:
            raise bad_gateway() from None
        return await self._bind_phone(principal, phone)

    async def sessions_of(self, principal: Principal) -> list[SessionModel]:
        return await self.sessions.list_active(principal.sub)
