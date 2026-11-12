"""用户：创建、更新（含管理员）、软删除，以及邮箱 / 手机号唯一性、角色 / 状态 / 头像校验。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.common.exceptions import HttpError, validation_error
from app.core.permissions.service import RoleId, StatusId
from app.core.users.repository import UNSET, Sort, UserRepository, parse_birthday
from app.core.users.serializers import CreatedRelations
from app.modules.files.models import FileModel
lazy from app.common.security import Passwords
lazy from app.core.session.service import SessionService
lazy from app.core.users.models import UserModel
lazy from app.modules.files.service import FilesService

type AccountCleanup = Callable[[int], Awaitable[None]]


@dataclass(slots=True)
class UserInput:
    """创建 / 更新的输入；值为 UNSET 表示请求中未提供（undefined）。"""

    email: Any = UNSET
    password: Any = UNSET
    provider: Any = UNSET
    first_name: Any = UNSET
    last_name: Any = UNSET
    phone: Any = UNSET
    nickname: Any = UNSET
    gender: Any = UNSET
    birthday: Any = UNSET
    photo: Any = UNSET
    role: Any = UNSET
    status: Any = UNSET
    wechat_open_id: Any = UNSET
    wechat_union_id: Any = UNSET
    alipay_open_id: Any = UNSET
    extra: dict[str, Any] = field(default_factory=dict)


_INPUT_FIELDS = {
    "email": "email",
    "password": "password",
    "provider": "provider",
    "first_name": "first_name",
    "last_name": "last_name",
    "phone": "phone",
    "nickname": "nickname",
    "gender": "gender",
    "birthday": "birthday",
    "photo": "photo",
    "role": "role",
    "status": "status",
    "wechat_open_id": "wechat_open_id",
    "wechat_union_id": "wechat_union_id",
    "alipay_open_id": "alipay_open_id",
}


def user_input_from(model: Any) -> UserInput:
    """由请求 DTO 构造输入：未提供的字段为 UNSET，显式 null 为 None。"""
    provided = model.model_fields_set
    values = {
        target: getattr(model, source)
        for source, target in _INPUT_FIELDS.items()
        if source in provided and hasattr(model, source)
    }
    for key in ("photo", "role", "status"):
        if isinstance(values.get(key), BaseModel):
            values[key] = values[key].model_dump(by_alias=True)
    return UserInput(**values)


def _given(value: Any) -> bool:
    return value is not UNSET and value is not None


def _id_of(ref: Any) -> Any:
    if isinstance(ref, dict):
        return ref.get("id")
    return getattr(ref, "id", None) if ref is not UNSET else None


def _valid_enum_id(value: Any, allowed: set[int]) -> bool:
    return str(value) in {str(item) for item in allowed}


class UsersService:
    def __init__(
        self,
        repository: UserRepository,
        files: FilesService,
        sessions: SessionService,
        passwords: Passwords,
        account_cleanup: AccountCleanup,
    ) -> None:
        self.repository = repository
        self.files = files
        self.sessions = sessions
        self.passwords = passwords
        self.account_cleanup = account_cleanup

    def _role(self, value: Any) -> int | None:
        role_id = _id_of(value)
        if not role_id:
            return None
        if not _valid_enum_id(role_id, {int(r) for r in RoleId}):
            raise validation_error("role", "roleNotExists")
        return int(role_id)

    def _status(self, value: Any) -> int | None:
        status_id = _id_of(value)
        if not status_id:
            return None
        if not _valid_enum_id(status_id, {int(s) for s in StatusId}):
            raise validation_error("status", "statusNotExists")
        return int(status_id)

    async def _photo(self, value: Any, owner: int | None) -> FileModel | object | None:
        if value is None:
            return None
        photo_id = _id_of(value)
        if not photo_id:
            return UNSET
        try:
            [file] = await self.files.assert_referenceable([UUID(str(photo_id))], owner)
        except HttpError as exc:
            if exc.status == 404:
                raise validation_error("photo", "imageNotExists") from None
            raise
        return file

    async def _ensure_email_free(self, email: str, current: int | None = None) -> None:
        existing = await self.repository.find_by_email(email)
        if existing is not None and not (current and existing.id == current):
            raise validation_error("email", "emailAlreadyExists")

    async def _ensure_phone_free(self, phone: str, current: int | None = None) -> None:
        existing = await self.repository.find_by_phone(phone)
        if existing is not None and not (current and existing.id == current):
            raise validation_error("phone", "phoneAlreadyExists")

    async def create(self, data: UserInput) -> tuple[UserModel, CreatedRelations]:
        password = (
            await self.passwords.hash_async(data.password)
            if _given(data.password) and data.password
            else None
        )
        email = None
        if _given(data.email) and data.email:
            await self._ensure_email_free(data.email)
            email = data.email
        phone = None
        if _given(data.phone) and data.phone:
            await self._ensure_phone_free(data.phone)
            phone = data.phone
        photo = await self._photo(data.photo, None)
        role_id = self._role(data.role)
        status_id = self._status(data.status)

        def or_none(value: Any) -> Any:
            return None if value is UNSET or value is None else value

        user = await self.repository.create(
            values={
                "firstName": or_none(data.first_name),
                "lastName": or_none(data.last_name),
                "email": email,
                "phone": phone,
                "nickname": or_none(data.nickname),
                "gender": 0 if not _given(data.gender) else data.gender,
                "birthday": parse_birthday(or_none(data.birthday)),
                "password": password,
                "provider": or_none(data.provider) or "email",
                "wechatOpenId": or_none(data.wechat_open_id),
                "wechatUnionId": or_none(data.wechat_union_id),
                "alipayOpenId": or_none(data.alipay_open_id),
            },
            photo_id=photo.id if isinstance(photo, FileModel) else None,
            role_id=role_id,
            status_id=status_id,
        )
        return user, CreatedRelations(
            role_id=role_id,
            status_id=status_id,
            photo=photo if isinstance(photo, FileModel) else None,
        )

    async def find_by_id(self, user_id: int) -> UserModel | None:
        return await self.repository.find_by_id(user_id)

    async def find_by_email(self, email: str | None) -> UserModel | None:
        return await self.repository.find_by_email(email)

    async def find_many(
        self, *, role_ids: list[int] | None, sort: list[Sort] | None, page: int, limit: int
    ) -> list[UserModel]:
        return await self.repository.find_many(role_ids=role_ids, sort=sort, page=page, limit=limit)

    async def update(self, user_id: int, data: UserInput) -> UserModel:
        password: Any = UNSET
        if _given(data.password) and data.password:
            current = await self.repository.find_by_id(user_id)
            if current is not None and current.password != data.password:
                password = await self.passwords.hash_async(data.password)
        email: Any = UNSET
        if _given(data.email) and data.email:
            await self._ensure_email_free(data.email, user_id)
            email = data.email
        elif data.email is None:
            email = None
        phone: Any = UNSET
        if _given(data.phone) and data.phone:
            await self._ensure_phone_free(data.phone, user_id)
            phone = data.phone
        elif data.phone is None:
            phone = None
        photo = await self._photo(data.photo, user_id)
        role_id = self._role(data.role)
        status_id = self._status(data.status)

        def keep(value: Any) -> Any:
            return value

        birthday = data.birthday
        return await self.repository.update(
            user_id,
            values={
                "firstName": keep(data.first_name),
                "lastName": keep(data.last_name),
                "email": email,
                "phone": phone,
                "nickname": keep(data.nickname),
                "gender": keep(data.gender),
                "birthday": parse_birthday(birthday) if birthday is not UNSET else UNSET,
                "password": password,
                "provider": keep(data.provider),
            },
            photo_id=photo.id if isinstance(photo, FileModel) else photo,
            role_id=role_id,
            status_id=status_id,
        )

    async def update_by_admin(self, user_id: int, data: UserInput) -> UserModel:
        user = await self.update(user_id, data)
        if _given(data.password) and data.password:
            await self.sessions.delete_by_user(user_id)
        return user

    async def remove(self, user_id: int) -> None:
        await self.sessions.delete_by_user(user_id)
        await self.repository.remove(user_id)
        await self.account_cleanup(user_id)
