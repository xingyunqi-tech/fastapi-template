"""与 nestjs ClassSerializerInterceptor 输出一致的用户序列化（键顺序、分组、`__entity`）。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from app.common.responses import iso_timestamp
lazy from app.modules.files.models import FileModel

if TYPE_CHECKING:
    from app.core.users.models import RoleModel, StatusModel, UserModel

PRIVATE_VISIBILITY: Final = "PRIVATE"


class View(StrEnum):
    ME = "me"
    ADMIN = "admin"
    PUBLIC = "public"


_GROUPED_FIELDS: Final = frozenset(
    {"email", "provider", "phone", "wechatOpenId", "wechatUnionId", "alipayOpenId"}
)


@dataclass(frozen=True, slots=True)
class FileUrls:
    backend_domain: str
    api_prefix: str

    def build(self, file: FileModel) -> str:
        if file.visibility == PRIVATE_VISIBILITY:
            return f"{self.backend_domain}/{self.api_prefix}/v1/files/private/{file.id}"
        return self.backend_domain + file.path


def serialize_file(file: FileModel, urls: FileUrls) -> dict[str, Any]:
    return {
        "id": str(file.id),
        "path": urls.build(file),
        "visibility": file.visibility or "PUBLIC",
        "mimeType": file.mime_type,
        "size": file.size,
    }


def _loaded_relation(relation: RoleModel | StatusModel | None, entity: str) -> Any:
    if relation is None:
        return None
    return {"id": relation.id, "name": relation.name, "__entity": entity}


@dataclass(frozen=True, slots=True)
class CreatedRelations:
    """`repository.save()` 返回的实体：关联只带调用方传入的 id，未传时整个键缺省。"""

    role_id: int | None
    status_id: int | None
    photo: FileModel | None


def serialize_user(
    user: UserModel,
    urls: FileUrls,
    *,
    view: View = View.ME,
    created: CreatedRelations | None = None,
) -> dict[str, Any]:
    if created is None:
        photo = user.photo
        relations: dict[str, Any] = {
            "role": _loaded_relation(user.role, "RoleEntity"),
            "status": _loaded_relation(user.status, "StatusEntity"),
        }
    else:
        photo = created.photo
        relations = {}
        if created.role_id is not None:
            relations["role"] = {"id": created.role_id}
        if created.status_id is not None:
            relations["status"] = {"id": created.status_id}
    body: dict[str, Any] = {
        "id": user.id,
        "email": user.email,
        "provider": user.provider,
        "firstName": user.first_name,
        "lastName": user.last_name,
        "phone": user.phone,
        "nickname": user.nickname,
        "gender": user.gender,
        "birthday": user.birthday.isoformat() if user.birthday else None,
        "photo": serialize_file(photo, urls) if photo is not None else None,
        **relations,
        "createdAt": iso_timestamp(user.created_at),
        "updatedAt": iso_timestamp(user.updated_at),
        "deletedAt": iso_timestamp(user.deleted_at) if user.deleted_at else None,
        "wechatOpenId": user.wechat_open_id,
        "wechatUnionId": user.wechat_union_id,
        "alipayOpenId": user.alipay_open_id,
    }
    if view is View.PUBLIC:
        return {key: value for key, value in body.items() if key not in _GROUPED_FIELDS}
    return body
