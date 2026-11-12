"""与 nestjs NotificationMapper 序列化输出一致：metadata / readAt 为空时不输出该键。"""

from typing import Any

from app.common.responses import iso_timestamp
lazy from app.modules.notifications.models import NotificationModel, NotificationSettingModel


def serialize_notification(notification: NotificationModel) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": notification.id,
        "userId": notification.user_id,
        "type": notification.type,
        "category": notification.category,
        "title": notification.title,
        "content": notification.content,
    }
    if notification.metadata_ is not None:
        body["metadata"] = notification.metadata_
    body["channels"] = notification.channels
    body["sentChannels"] = notification.sent_channels
    body["isRead"] = notification.is_read
    if notification.read_at is not None:
        body["readAt"] = iso_timestamp(notification.read_at)
    body["createdAt"] = iso_timestamp(notification.created_at)
    body["updatedAt"] = iso_timestamp(notification.updated_at)
    body["deletedAt"] = (
        iso_timestamp(notification.deleted_at) if notification.deleted_at is not None else None
    )
    return body


def serialize_setting(setting: NotificationSettingModel) -> dict[str, Any]:
    return {
        "id": setting.id,
        "userId": setting.user_id,
        "category": setting.category,
        "enableInApp": setting.enable_in_app,
        "enableEmail": setting.enable_email,
        "enableSms": setting.enable_sms,
        "enablePush": setting.enable_push,
        "createdAt": iso_timestamp(setting.created_at),
        "updatedAt": iso_timestamp(setting.updated_at),
    }
