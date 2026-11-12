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
from app.modules.config.models import ConfigModel
from app.modules.regions.models import RegionModel
lazy from app.config import Settings

DATA_DIR: Final = Path(__file__).resolve().parent / "seed_data"
DEFAULT_PASSWORD: Final = "Secret00"  # noqa: S105
REGION_BATCH: Final = 500


def _load(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _md5(text: str) -> str:
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


def _normalize_code(code: str) -> str:
    return code.ljust(12, "0")


def _regions() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sort = 0

    def add(code: str, name: str, level: int, parent: str | None) -> str:
        nonlocal sort
        normalized = _normalize_code(code)
        rows.append(
            {"code": normalized, "name": name, "level": level, "parent_code": parent, "sort": sort}
        )
        sort += 1
        return normalized

    for province in _load("region-data.json"):
        province_code = add(province["code"], province["name"], 1, None)
        for city in province.get("children") or []:
            city_code = add(city["code"], city["name"], 2, province_code)
            for district in city.get("children") or []:
                add(district["code"], district["name"], 3, city_code)
    return rows


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
    if not await session.scalar(select(func.count()).select_from(RegionModel)):
        regions = _regions()
        for start in range(0, len(regions), REGION_BATCH):
            await session.execute(insert(RegionModel).values(regions[start : start + REGION_BATCH]))
    await _seed_users(session, settings)
    await session.execute(
        insert(ConfigModel)
        .values(
            [
                {"key": row["key"], "value": row["value"], "is_public": row["isPublic"]}
                for row in data["config"]
            ]
        )
        .on_conflict_do_nothing()
    )


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
    passwords = Passwords(settings.auth.password_hasher)
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
