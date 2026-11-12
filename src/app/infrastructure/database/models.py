"""汇总全部 ORM 模型，供 Alembic 与测试建表使用。"""

from app.core.permissions.models import PermissionModel, RolePermissionModel
from app.core.session.models import SessionModel
from app.core.users.models import RoleModel, StatusModel, UserModel
from app.infrastructure.account_cleanup.models import AccountCleanupModel
from app.infrastructure.alerting.models import AdminAlertModel
from app.infrastructure.audit.models import AuditLogModel
from app.infrastructure.database.base import Base
from app.modules.addresses.models import AddressModel
from app.modules.config.models import ConfigModel
from app.modules.consents.models import UserConsentModel
from app.modules.favorites.models import FavoriteModel
from app.modules.feedbacks.models import FeedbackModel
from app.modules.files.models import FileModel
from app.modules.notifications.models import NotificationModel, NotificationSettingModel
from app.modules.regions.models import RegionModel

__all__ = [
    "AccountCleanupModel",
    "AddressModel",
    "AdminAlertModel",
    "AuditLogModel",
    "Base",
    "ConfigModel",
    "FavoriteModel",
    "FeedbackModel",
    "FileModel",
    "NotificationModel",
    "NotificationSettingModel",
    "PermissionModel",
    "RegionModel",
    "RoleModel",
    "RolePermissionModel",
    "SessionModel",
    "StatusModel",
    "UserConsentModel",
    "UserModel",
]
