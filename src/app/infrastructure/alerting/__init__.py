"""运营告警：写 `admin_alert`（同一 dedupKey 有未确认告警时去重），并给管理员发站内通知。"""

import logging
from enum import StrEnum
from typing import Any, Final, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.alerting.models import AdminAlertModel
from app.infrastructure.metrics import record_admin_alert, record_admin_alert_deduplicated
lazy from app.core.users.models import UserModel
lazy from app.infrastructure.database.session import Database
lazy from app.modules.notifications.models import NotificationModel, NotificationSettingModel

logger = logging.getLogger("app.alerting")

ALERT_RECIPIENT_LIMIT: Final = 50
ADMIN_ROLE_ID: Final = 1


class AlertLevel(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


NOTIFICATION_TYPE_BY_LEVEL: Final = {
    AlertLevel.INFO: "INFO",
    AlertLevel.WARN: "WARNING",
    AlertLevel.CRITICAL: "ERROR",
}


class AlertReporter(Protocol):
    async def raise_(
        self,
        type_: str,
        level: AlertLevel,
        message: str,
        payload: dict[str, Any] | None = None,
        dedup_key: str | None = None,
    ) -> AdminAlertModel | None: ...


class AdminAlertService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def raise_(
        self,
        type_: str,
        level: AlertLevel,
        message: str,
        payload: dict[str, Any] | None = None,
        dedup_key: str | None = None,
    ) -> AdminAlertModel | None:
        """同一 dedupKey 已有未确认告警或写入失败时返回 None，不抛错。"""
        key = dedup_key or type_
        try:
            async with self.database.transaction() as session:
                alert = await _create_if_no_open(session, type_, level, message, payload, key)
        except Exception as error:
            logger.error("Failed to record alert %s: %s", key, error)
            return None
        if alert is None:
            record_admin_alert_deduplicated(type_)
            return None
        record_admin_alert(type_, level)
        logger.warning("Alert raised [%s] %s: %s", level, type_, message)
        await self._notify_admins(alert)
        return alert

    async def _notify_admins(self, alert: AdminAlertModel) -> None:
        try:
            async with self.database.transaction() as session:
                await _notify_admins(session, alert)
        except Exception as error:
            logger.error("Failed to notify admins of alert %s: %s", alert.id, error)


async def _create_if_no_open(
    session: AsyncSession,
    type_: str,
    level: AlertLevel,
    message: str,
    payload: dict[str, Any] | None,
    dedup_key: str,
) -> AdminAlertModel | None:
    statement = (
        insert(AdminAlertModel)
        .values(type=type_, level=str(level), message=message, payload=payload, dedup_key=dedup_key)
        .on_conflict_do_nothing()
        .returning(AdminAlertModel)
    )
    return (await session.scalars(statement)).one_or_none()


async def _notify_admins(session: AsyncSession, alert: AdminAlertModel) -> None:
    admin_ids = await session.scalars(
        select(UserModel.id)
        .where(UserModel.role_id == ADMIN_ROLE_ID, UserModel.deleted_at.is_(None))
        .order_by(UserModel.id)
        .limit(ALERT_RECIPIENT_LIMIT)
    )
    for admin_id in admin_ids.all():
        setting = await session.scalar(
            select(NotificationSettingModel).where(
                NotificationSettingModel.user_id == admin_id,
                NotificationSettingModel.category == "SYSTEM",
            )
        )
        channels = ["IN_APP"] if setting is None or setting.enable_in_app else []
        session.add(
            NotificationModel(
                user_id=admin_id,
                type=NOTIFICATION_TYPE_BY_LEVEL[AlertLevel(alert.level)],
                category="SYSTEM",
                title=f"[{alert.level}] {alert.type}"[:200],
                content=alert.message,
                metadata_={"alertId": alert.id, "dedupKey": alert.dedup_key},
                channels=channels,
                sent_channels={},
                is_read=False,
            )
        )
    await session.flush()
