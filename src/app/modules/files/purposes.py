"""约定 §11.5 的用途：`avatar`（约定）与 `attachment`（本服务追加）；类型按文件头魔数识别。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.security import rbac
from app.security.rbac import Role

MIB: Final = 1024 * 1024
SNIFF_BYTES: Final = 16
IMAGE_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/webp"})

FILE_WRITE: Final = rbac.register("file:write", roles=[Role.USER])


class Purpose(StrEnum):
    AVATAR = "avatar"
    ATTACHMENT = "attachment"


@dataclass(frozen=True, slots=True)
class PurposeRule:
    content_types: frozenset[str]
    max_size: int


RULES: Final[dict[Purpose, PurposeRule]] = {
    Purpose.AVATAR: PurposeRule(IMAGE_TYPES, 5 * MIB),
    Purpose.ATTACHMENT: PurposeRule(IMAGE_TYPES | {"image/gif", "application/pdf"}, 20 * MIB),
}
MAX_SIZE: Final = max(rule.max_size for rule in RULES.values())


def sniff(head: bytes) -> str | None:
    """按文件头识别白名单内的类型，无法识别时为 None。"""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def is_image(content_type: str) -> bool:
    return content_type.startswith("image/")
