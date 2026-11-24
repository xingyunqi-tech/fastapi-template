"""`File.url`：s3 为预签名 GET；local 为 `getFileContent` 的 HMAC-SHA256 签名地址。"""

import hmac
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.modules.files.purposes import is_image
from app.modules.files.storage import LocalStorage, S3Storage, build_storage, content_disposition

if TYPE_CHECKING:
    from uuid import UUID

    from app.config import Settings
    from app.modules.files.models import FileModel

CONTENT_PATH: Final = "/api/v1/files/{id}/content"


def url_secret(settings: Settings) -> bytes:
    """未设置 `STORAGE_URL_SECRET` 时由 `AUTH_SIGNING_KEY` 经 HKDF 派生（生产要求显式设置）。"""
    secret = settings.storage.url_secret
    if secret is not None:
        return secret.get_secret_value().encode()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"file-url").derive(
        settings.auth.signing_pem().encode()
    )


def signature(secret: bytes, file_id: UUID | str, expires: int) -> str:
    return hmac.new(secret, f"{file_id}:{expires}".encode(), "sha256").hexdigest()


@dataclass(frozen=True, slots=True)
class FileUrls:
    storage: LocalStorage | S3Storage
    secret: bytes
    ttl_seconds: int

    @classmethod
    def from_settings(cls, settings: Settings) -> FileUrls:
        return cls(
            build_storage(settings.storage),
            url_secret(settings),
            int(settings.storage.url_ttl.total_seconds()),
        )

    def build(self, file: FileModel, now: float | None = None) -> str:
        if isinstance(self.storage, S3Storage):
            disposition = None if is_image(file.content_type) else content_disposition(file.name)
            return self.storage.presigned_get(file.storage_key, self.ttl_seconds, disposition)
        expires = int(now if now is not None else time.time()) + self.ttl_seconds
        query = urlencode(
            {"expires": expires, "signature": signature(self.secret, file.id, expires)}
        )
        return f"{CONTENT_PATH.format(id=file.id)}?{query}"

    def verify(self, file_id: UUID, expires: int, given: str, now: float | None = None) -> bool:
        if expires < (now if now is not None else time.time()):
            return False
        expected = signature(self.secret, file_id, expires)
        return hmac.compare_digest(expected.encode(), given.encode())
