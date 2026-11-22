"""种子数据：无 active admin 时创建管理员账号（角色为代码常量，不入库）。"""

from datetime import UTC, datetime
from typing import Final

lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.users.models import UserModel, UserRoleModel, UserStatus
from app.identity.users.repository import UserRepository
from app.security.passwords import Passwords
from app.security.rbac import Role
lazy from app.config import Settings

DEVELOPMENT_ADMIN_EMAIL: Final = "admin@example.com"
DEVELOPMENT_ADMIN_PASSWORD: Final = "Secret00"  # noqa: S105


async def seed(session: AsyncSession, settings: Settings) -> bool:
    """非生产为 `admin@example.com` / `Secret00`；生产取 `SEED_ADMIN_*`，未设置时不创建。"""
    if settings.app.is_production:
        auth = settings.auth
        email = (auth.seed_admin_email or "").strip().lower() or None
        secret = auth.seed_admin_password
        password = secret.get_secret_value() if secret is not None else None
    else:
        email, password = DEVELOPMENT_ADMIN_EMAIL, DEVELOPMENT_ADMIN_PASSWORD
    if email is None or password is None or await UserRepository(session).active_admin_ids():
        return False
    passwords = Passwords(concurrency=settings.auth.hash_concurrency)
    session.add(
        UserModel(
            email=email,
            email_verified_at=datetime.now(UTC),
            password_hash=passwords.hash(password),
            display_name="Admin",
            status=UserStatus.ACTIVE.value,
            role_rows=[UserRoleModel(role=Role.ADMIN.value), UserRoleModel(role=Role.USER.value)],
        )
    )
    await session.flush()
    return True
