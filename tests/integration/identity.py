"""身份相关的测试辅助：种子用户、登录与 Bearer 头。"""

from typing import TYPE_CHECKING, Any, Final

from app.identity.users.models import UserModel, UserRoleModel, UserStatus
from app.infrastructure.mail import LoggingMailer
from app.security.passwords import Passwords
from app.security.rbac import Role

if TYPE_CHECKING:
    import httpx2 as httpx
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

PASSWORD: Final = "Secret00"
ADMIN_EMAIL: Final = "admin@example.com"
USER_EMAIL: Final = "john.doe@example.com"


async def seed_users(session: AsyncSession) -> None:
    """模板库中的普通用户（邮箱未验证）；管理员由 `app seed` 创建。"""
    session.add(
        UserModel(
            email=USER_EMAIL,
            password_hash=Passwords().hash(PASSWORD),
            display_name="John Doe",
            status=UserStatus.ACTIVE.value,
            role_rows=[UserRoleModel(role=Role.USER.value)],
        )
    )
    await session.flush()


async def login(client: httpx.AsyncClient, email: str, password: str = PASSWORD) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/email/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def auth_headers(client: httpx.AsyncClient, email: str = USER_EMAIL) -> dict[str, str]:
    return bearer((await login(client, email))["accessToken"])


def sent_mail(app: FastAPI) -> LoggingMailer:
    mailer = app.state.context.mailer
    assert isinstance(mailer, LoggingMailer)
    return mailer
