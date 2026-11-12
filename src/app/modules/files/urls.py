"""文件对外 URL（nestjs file-url.ts buildFileUrl）。"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.config import FileDriver
from app.modules.files.purposes import FileVisibility
from app.modules.files.storage import S3Options, presigned_get_url

if TYPE_CHECKING:
    from app.config import FileSettings, Settings
    from app.modules.files.models import FileModel


@dataclass(frozen=True, slots=True)
class FileUrls:
    """local：公开文件为 BACKEND_DOMAIN + path，私有文件走鉴权接口；S3 系：预签名 GET URL。"""

    backend_domain: str
    api_prefix: str
    file: FileSettings | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> FileUrls:
        return cls(settings.app.backend_domain, settings.app.api_prefix, settings.file)

    def private_api_path(self, file_id: object) -> str:
        return f"/{self.api_prefix}/v1/files/private/{file_id}"

    def build(self, file: FileModel) -> str:
        if self.file is None or self.file.driver is FileDriver.LOCAL:
            if file.visibility == FileVisibility.PRIVATE:
                return self.backend_domain + self.private_api_path(file.id)
            return self.backend_domain + file.path
        return presigned_get_url(S3Options.from_settings(self.file), file.path)
