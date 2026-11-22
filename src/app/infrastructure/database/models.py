"""汇总全部 ORM 模型，供 Alembic 与测试建表使用。"""

from app.identity.account_deletion.models import AccountDeletionModel
from app.identity.auth.models import VerificationTokenModel
from app.identity.sessions.models import RefreshTokenModel, SessionModel
from app.identity.users.models import UserModel, UserRoleModel
from app.infrastructure.audit.models import AuditLogModel
from app.infrastructure.database.base import Base
from app.modules.files.models import FileModel

__all__ = [
    "AccountDeletionModel",
    "AuditLogModel",
    "Base",
    "FileModel",
    "RefreshTokenModel",
    "SessionModel",
    "UserModel",
    "UserRoleModel",
    "VerificationTokenModel",
]
