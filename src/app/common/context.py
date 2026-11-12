"""应用级单例（配置、数据库、缓存、密码哈希、邮件），挂在 app.state 上供依赖获取。"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.common.security import Passwords
from app.config import Settings
from app.core.users.serializers import FileUrls
from app.infrastructure.cache import Cache
from app.infrastructure.database.session import Database
from app.integrations.mail import Mailer


@dataclass(slots=True)
class AppContext:
    settings: Settings
    database: Database
    cache: Cache
    passwords: Passwords
    mailer: Mailer

    @property
    def file_urls(self) -> FileUrls:
        return FileUrls(self.settings.app.backend_domain, self.settings.app.api_prefix)


def _context(request: Request) -> AppContext:
    context: AppContext = request.app.state.context
    return context


Context = Annotated[AppContext, Depends(_context)]
