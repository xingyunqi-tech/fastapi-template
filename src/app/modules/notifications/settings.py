from typing import Final

from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.models import NotificationSettingModel

CATEGORIES: Final = ("ORDER", "PAYMENT", "SYSTEM", "PROMOTION", "ACTIVITY", "STUDY")


async def initialize_default_settings(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        insert(NotificationSettingModel)
        .values([{"user_id": user_id, "category": category} for category in CATEGORIES])
        .on_conflict_do_nothing(constraint="UQ_notification_setting_userId_category")
    )
