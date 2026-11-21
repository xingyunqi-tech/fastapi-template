"""种子数据，与 nestjs seeds 一致：已存在的数据不覆盖。"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.security import Passwords
from app.core.permissions.models import PermissionModel, RolePermissionModel
from app.core.permissions.service import RoleId, StatusId
from app.core.users.models import RoleModel, StatusModel, UserModel
lazy from app.config import Settings

DATA_DIR: Final = Path(__file__).resolve().parent / "seed_data"
DEFAULT_PASSWORD: Final = "Secret00"  # noqa: S105


def _load(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _md5(text: str) -> str:
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


async def seed(session: AsyncSession, settings: Settings) -> None:
    data = _load("seed.json")
    for model, rows in ((RoleModel, data["roles"]), (StatusModel, data["statuses"])):
        await session.execute(insert(model).values(rows).on_conflict_do_nothing())
    await session.execute(
        insert(PermissionModel).values(data["permissions"]).on_conflict_do_nothing()
    )
    await session.execute(
        insert(RolePermissionModel)
        .values(
            [
                {"role_id": row["roleId"], "permission_id": row["permissionId"]}
                for row in data["rolePermissions"]
            ]
        )
        .on_conflict_do_nothing()
    )
    await _seed_users(session, settings)


async def _count_role(session: AsyncSession, role: RoleId) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(UserModel)
            .where(UserModel.role_id == role, UserModel.deleted_at.is_(None))
        )
        or 0
    )


async def _seed_users(session: AsyncSession, settings: Settings) -> None:
    passwords = Passwords(settings.auth.password_hasher, concurrency=settings.auth.hash_concurrency)
    production = settings.app.is_production
    admin_email = (
        (os.environ.get("SEED_ADMIN_EMAIL") or "").strip().lower() or None
        if production
        else "admin@example.com"
    )
    admin_password = os.environ.get("SEED_ADMIN_PASSWORD") if production else DEFAULT_PASSWORD
    if not await _count_role(session, RoleId.ADMIN) and admin_email and admin_password:
        session.add(
            UserModel(
                first_name="Super",
                last_name="Admin",
                email=admin_email,
                password=passwords.hash(_md5(admin_password)),
                role_id=RoleId.ADMIN,
                status_id=StatusId.ACTIVE,
            )
        )
        await session.flush()
    if not await _count_role(session, RoleId.USER) and not production:
        session.add(
            UserModel(
                first_name="John",
                last_name="Doe",
                email="john.doe@example.com",
                password=passwords.hash(_md5(DEFAULT_PASSWORD)),
                role_id=RoleId.USER,
                status_id=StatusId.ACTIVE,
            )
        )
        await session.flush()
