import json
from dataclasses import dataclass
from typing import Any, Final
lazy from collections.abc import Sequence
lazy from datetime import datetime

from sqlalchemy import ColumnElement, delete, func, null, select, text, update
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.models import NotificationModel, NotificationSettingModel

IN_APP: Final = "IN_APP"

_RECORD_CHANNEL_RESULT: Final = text(
    'UPDATE "notification" SET "sentChannels" = "sentChannels" || CAST(:patch AS jsonb), '
    '"updatedAt" = CURRENT_TIMESTAMP WHERE "id" = :id AND "deletedAt" IS NULL'
)
_INSERT_IN_APP_BATCH: Final = text(
    'INSERT INTO "notification" ("userId", "type", "category", "title", "content", "metadata", '
    '"channels") '
    'SELECT target."userId", CAST(:type AS varchar), CAST(:category AS varchar), '
    "CAST(:title AS varchar), CAST(:content AS text), CAST(:metadata AS jsonb), "
    "CAST(:channels AS jsonb) "
    'FROM unnest(CAST(:user_ids AS int[])) AS target("userId") '
    "WHERE NOT EXISTS ("
    'SELECT 1 FROM "notification_setting" AS setting '
    'WHERE setting."userId" = target."userId" AND setting."category" = CAST(:category AS varchar) '
    'AND NOT setting."enableInApp") '
    'RETURNING "id"'
)


@dataclass(frozen=True, slots=True)
class InAppBatch:
    user_ids: Sequence[int]
    type: str
    category: str
    title: str
    content: str
    metadata: dict[str, Any] | None


def _visible(user_id: int) -> list[ColumnElement[bool]]:
    """用户可见的通知：未删除且投递渠道包含站内信。"""
    return [
        NotificationModel.user_id == user_id,
        NotificationModel.channels.contains([IN_APP]),
        NotificationModel.deleted_at.is_(None),
    ]


def _filters(category: str | None, is_read: bool | None) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if category:
        conditions.append(NotificationModel.category == category)
    if is_read is not None:
        conditions.append(NotificationModel.is_read == is_read)
    return conditions


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        type_: str,
        category: str,
        title: str,
        content: str,
        metadata: dict[str, Any] | None,
        channels: list[str],
    ) -> NotificationModel:
        notification = NotificationModel(
            user_id=user_id,
            type=type_,
            category=category,
            title=title,
            content=content,
            metadata_=metadata if metadata is not None else null(),
            channels=channels,
            sent_channels={},
            is_read=False,
        )
        self.session.add(notification)
        await self.session.flush()
        await self.session.refresh(notification)
        return notification

    async def insert_in_app_batch(self, batch: InAppBatch) -> int:
        if not batch.user_ids:
            return 0
        rows = await self.session.execute(
            _INSERT_IN_APP_BATCH,
            {
                "user_ids": list(batch.user_ids),
                "type": batch.type,
                "category": batch.category,
                "title": batch.title,
                "content": batch.content,
                "metadata": None if batch.metadata is None else json.dumps(batch.metadata),
                "channels": json.dumps([IN_APP]),
            },
        )
        return len(rows.all())

    async def find_many(
        self, user_id: int, *, category: str | None, is_read: bool | None, page: int, limit: int
    ) -> list[NotificationModel]:
        statement = (
            select(NotificationModel)
            .where(*_visible(user_id), *_filters(category, is_read))
            .order_by(NotificationModel.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def count(self, user_id: int, *, category: str | None, is_read: bool | None) -> int:
        statement = (
            select(func.count())
            .select_from(NotificationModel)
            .where(*_visible(user_id), *_filters(category, is_read))
        )
        return int(await self.session.scalar(statement) or 0)

    async def find_by_id_and_user(
        self, notification_id: int, user_id: int
    ) -> NotificationModel | None:
        return await self.session.scalar(
            select(NotificationModel).where(
                NotificationModel.id == notification_id, *_visible(user_id)
            )
        )

    async def mark_as_read(self, notification_id: int, read_at: datetime) -> NotificationModel:
        statement = (
            update(NotificationModel)
            .where(NotificationModel.id == notification_id)
            .values(is_read=True, read_at=read_at, updated_at=func.now())
            .returning(NotificationModel)
            .execution_options(populate_existing=True)
        )
        return (await self.session.scalars(statement)).one()

    async def mark_all_as_read(self, user_id: int, read_at: datetime) -> None:
        await self.session.execute(
            update(NotificationModel)
            .where(
                NotificationModel.user_id == user_id,
                NotificationModel.is_read.is_(False),
                NotificationModel.channels.contains([IN_APP]),
            )
            .values(is_read=True, read_at=read_at, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )

    async def count_unread(self, user_id: int) -> int:
        return await self.count(user_id, category=None, is_read=False)

    async def count_unread_by_category(self, user_id: int) -> dict[str, int]:
        rows = await self.session.execute(
            select(NotificationModel.category, func.count())
            .where(*_visible(user_id), NotificationModel.is_read.is_(False))
            .group_by(NotificationModel.category)
        )
        return {category: int(count) for category, count in rows.tuples()}

    async def record_channel_result(
        self, notification_id: int, channel: str, result: dict[str, Any]
    ) -> None:
        await self.session.execute(
            _RECORD_CHANNEL_RESULT,
            {"id": notification_id, "patch": json.dumps({channel: result})},
        )

    async def soft_delete(self, notification_id: int) -> None:
        await self.session.execute(
            update(NotificationModel)
            .where(NotificationModel.id == notification_id, NotificationModel.deleted_at.is_(None))
            .values(deleted_at=func.now(), updated_at=func.now())
            .execution_options(synchronize_session=False)
        )

    async def remove_by_user(self, user_id: int) -> None:
        await self.session.execute(
            delete(NotificationModel).where(NotificationModel.user_id == user_id)
        )


class NotificationSettingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find(self, user_id: int, category: str) -> NotificationSettingModel | None:
        return await self.session.scalar(
            select(NotificationSettingModel).where(
                NotificationSettingModel.user_id == user_id,
                NotificationSettingModel.category == category,
            )
        )

    async def find_all(self, user_id: int) -> list[NotificationSettingModel]:
        return list(
            await self.session.scalars(
                select(NotificationSettingModel).where(NotificationSettingModel.user_id == user_id)
            )
        )

    async def create(
        self, user_id: int, category: str, flags: dict[str, bool]
    ) -> NotificationSettingModel:
        setting = NotificationSettingModel(user_id=user_id, category=category, **flags)
        self.session.add(setting)
        await self.session.flush()
        await self.session.refresh(setting)
        return setting

    async def update(
        self, setting: NotificationSettingModel, flags: dict[str, bool]
    ) -> NotificationSettingModel:
        """只有取值变化时才写入并刷新 updatedAt（TypeORM save 的脏检查）。"""
        changes = {key: value for key, value in flags.items() if getattr(setting, key) != value}
        if not changes:
            return setting
        statement = (
            update(NotificationSettingModel)
            .where(NotificationSettingModel.id == setting.id)
            .values(**changes, updated_at=func.now())
            .returning(NotificationSettingModel)
            .execution_options(populate_existing=True)
        )
        return (await self.session.scalars(statement)).one()

    async def remove_by_user(self, user_id: int) -> None:
        await self.session.execute(
            delete(NotificationSettingModel).where(NotificationSettingModel.user_id == user_id)
        )
