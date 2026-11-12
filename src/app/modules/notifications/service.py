"""站内通知与通知设置（nestjs NotificationsService）：按用户设置过滤渠道，提交后投递站外渠道。"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
lazy from collections.abc import Sequence

lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import not_found
from app.common.responses import iso_timestamp
from app.infrastructure.database.session import after_commit
from app.modules.notifications.repository import (
    InAppBatch,
    NotificationRepository,
    NotificationSettingRepository,
)
from app.modules.notifications.settings import initialize_default_settings
lazy from app.infrastructure.queue import TaskQueue
lazy from app.infrastructure.queue.notification import queue_external_notifications
lazy from app.modules.notifications.models import NotificationModel, NotificationSettingModel

logger = logging.getLogger("app.notifications")


class NotificationType(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"


class NotificationCategory(StrEnum):
    ORDER = "ORDER"
    PAYMENT = "PAYMENT"
    SYSTEM = "SYSTEM"
    PROMOTION = "PROMOTION"
    ACTIVITY = "ACTIVITY"
    STUDY = "STUDY"


class NotificationChannel(StrEnum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    SMS = "SMS"
    PUSH = "PUSH"


_CHANNEL_FLAGS = {
    NotificationChannel.IN_APP: "enable_in_app",
    NotificationChannel.EMAIL: "enable_email",
    NotificationChannel.SMS: "enable_sms",
    NotificationChannel.PUSH: "enable_push",
}


@dataclass(frozen=True, slots=True)
class CreateNotification:
    user_id: int
    type: str
    category: str
    title: str
    content: str
    channels: Sequence[str]
    metadata: dict[str, Any] | None = field(default=None)


@dataclass(frozen=True, slots=True)
class SettingsInput:
    category: str
    enable_in_app: bool
    enable_email: bool
    enable_sms: bool
    enable_push: bool

    def flags(self) -> dict[str, bool]:
        return {
            "enable_in_app": self.enable_in_app,
            "enable_email": self.enable_email,
            "enable_sms": self.enable_sms,
            "enable_push": self.enable_push,
        }


def _now_ms() -> datetime:
    """JS `new Date()`：毫秒精度，写入 timestamp 列时为 UTC。"""
    now = datetime.now(UTC).replace(tzinfo=None)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def _not_found() -> Exception:
    return not_found({"error": "notificationNotFound", "message": "Notification not found"})


class NotificationsService:
    def __init__(self, session: AsyncSession, queue: TaskQueue | None = None) -> None:
        self.session = session
        self.queue = queue
        self.notifications = NotificationRepository(session)
        self.settings = NotificationSettingRepository(session)

    async def create(self, data: CreateNotification) -> NotificationModel:
        logger.info("Creating notification for user: %s", data.user_id)
        setting = await self.settings.find(data.user_id, data.category)
        channels = list(data.channels)
        if setting is not None:
            channels = [
                channel
                for channel in channels
                if channel in _CHANNEL_FLAGS
                and getattr(setting, _CHANNEL_FLAGS[NotificationChannel(channel)])
            ]
        notification = await self.notifications.create(
            user_id=data.user_id,
            type_=data.type,
            category=data.category,
            title=data.title,
            content=data.content,
            metadata=data.metadata,
            channels=channels,
        )
        self._queue_external(notification)
        logger.info("Notification created successfully: %s", notification.id)
        return notification

    def _queue_external(self, notification: NotificationModel) -> None:
        queue = self.queue
        if queue is None:
            if any(channel != NotificationChannel.IN_APP for channel in notification.channels):
                logger.warning(
                    "Redis disabled, external channels skipped for notification %s",
                    notification.id,
                )
            return

        async def enqueue() -> None:
            await queue_external_notifications(queue, notification)

        after_commit(self.session, enqueue)

    async def create_in_app_batch(self, batch: InAppBatch) -> int:
        """同一事务内给多个用户写同一条站内信，跳过关闭了该分类站内信的用户，返回写入数。"""
        return await self.notifications.insert_in_app_batch(batch)

    async def record_channel_result(
        self, notification_id: int, channel: str, *, sent: bool, error: str | None = None
    ) -> None:
        result: dict[str, Any] = {"sent": sent}
        if error:
            result["error"] = error
        result["sentAt"] = iso_timestamp(datetime.now(UTC))
        await self.notifications.record_channel_result(notification_id, channel, result)

    async def find_many_with_count(
        self, user_id: int, *, category: str | None, is_read: bool | None, page: int, limit: int
    ) -> tuple[list[NotificationModel], int]:
        data = await self.notifications.find_many(
            user_id, category=category, is_read=is_read, page=page, limit=limit
        )
        total = await self.notifications.count(user_id, category=category, is_read=is_read)
        return data, total

    async def find_one(self, notification_id: int, user_id: int) -> NotificationModel:
        notification = await self.notifications.find_by_id_and_user(notification_id, user_id)
        if notification is None:
            raise _not_found()
        return notification

    async def mark_as_read(self, notification_id: int, user_id: int) -> NotificationModel:
        notification = await self.notifications.find_by_id_and_user(notification_id, user_id)
        if notification is None:
            raise _not_found()
        updated = await self.notifications.mark_as_read(notification_id, _now_ms())
        logger.info("Notification %s marked as read by user %s", notification_id, user_id)
        return updated

    async def mark_all_as_read(self, user_id: int) -> None:
        await self.notifications.mark_all_as_read(user_id, _now_ms())
        logger.info("All notifications marked as read for user %s", user_id)

    async def get_unread_count(self, user_id: int) -> int:
        return await self.notifications.count_unread(user_id)

    async def get_unread_count_by_category(self, user_id: int) -> dict[str, int]:
        return await self.notifications.count_unread_by_category(user_id)

    async def remove(self, notification_id: int, user_id: int) -> None:
        if await self.notifications.find_by_id_and_user(notification_id, user_id) is None:
            raise _not_found()
        await self.notifications.soft_delete(notification_id)
        logger.info("Notification %s deleted by user %s", notification_id, user_id)

    async def get_settings(self, user_id: int) -> list[NotificationSettingModel]:
        return await self.settings.find_all(user_id)

    async def update_settings(self, user_id: int, data: SettingsInput) -> NotificationSettingModel:
        setting = await self.settings.find(user_id, data.category)
        if setting is None:
            setting = await self.settings.create(user_id, data.category, data.flags())
        else:
            setting = await self.settings.update(setting, data.flags())
        logger.info(
            "Notification settings updated for user %s, category %s", user_id, data.category
        )
        return setting

    async def initialize_default_settings(self, user_id: int) -> None:
        await initialize_default_settings(self.session, user_id)
        logger.info("Default notification settings initialized for user %s", user_id)
