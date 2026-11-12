from typing import Annotated, Any, Final

lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.context import AppContext, Context
from app.common.pagination import pagination
from app.common.params import Body, IdParam, Query
from app.common.routing import ApiRouter, Auth
from app.contract.schemas import (
    Notification,
    NotificationSetting,
    PaginationNotificationResponseDto,
    UpdateNotificationSettingsDto,
)
from app.core.auth.deps import CurrentUser
from app.infrastructure.database.session import DbSession
from app.modules.notifications.schemas import QueryNotificationDto
from app.modules.notifications.serializers import serialize_notification, serialize_setting
from app.modules.notifications.service import NotificationsService, SettingsInput

MAX_LIMIT: Final = 50

router = ApiRouter(prefix="/v1/notifications", tags=["Notifications"])


def _service(context: AppContext, session: AsyncSession) -> NotificationsService:
    return NotificationsService(session, context.queue)


@router.endpoint(
    "GET",
    "",
    operation_id="getNotifications",
    auth=Auth.JWT,
    response_model=PaginationNotificationResponseDto,
    summary="获取通知列表",
)
async def get_notifications(
    user: CurrentUser,
    query: Annotated[QueryNotificationDto, Query(QueryNotificationDto)],
    context: Context,
    session: DbSession,
) -> dict[str, Any]:
    page = int(query.page) if query.page is not None else 1
    limit = min(int(query.limit) if query.limit is not None else 10, MAX_LIMIT)
    data, total = await _service(context, session).find_many_with_count(
        user.id, category=query.category, is_read=query.is_read, page=page, limit=limit
    )
    return pagination([serialize_notification(n) for n in data], total, page, limit)


@router.endpoint(
    "GET",
    "/unread-count",
    operation_id="getUnreadCount",
    auth=Auth.JWT,
    response_model=int,
    summary="获取未读通知数量",
)
async def get_unread_count(user: CurrentUser, context: Context, session: DbSession) -> int:
    return await _service(context, session).get_unread_count(user.id)


@router.endpoint(
    "GET",
    "/settings",
    operation_id="getNotificationSettings",
    auth=Auth.JWT,
    response_model=list[NotificationSetting],
    summary="获取通知设置",
)
async def get_settings(
    user: CurrentUser, context: Context, session: DbSession
) -> list[dict[str, Any]]:
    return [serialize_setting(s) for s in await _service(context, session).get_settings(user.id)]


@router.endpoint(
    "GET",
    "/{id}",
    operation_id="getNotification",
    auth=Auth.JWT,
    response_model=Notification,
    summary="获取通知详情",
)
async def get_notification(
    user: CurrentUser, id: IdParam, context: Context, session: DbSession
) -> dict[str, Any]:
    return serialize_notification(await _service(context, session).find_one(id, user.id))


@router.endpoint(
    "POST",
    "/read-all",
    operation_id="markAllNotificationsAsRead",
    auth=Auth.JWT,
    response_model=None,
    summary="标记所有通知为已读",
)
async def mark_all_as_read(user: CurrentUser, context: Context, session: DbSession) -> None:
    await _service(context, session).mark_all_as_read(user.id)


@router.endpoint(
    "PUT",
    "/settings",
    operation_id="updateNotificationSettings",
    auth=Auth.JWT,
    response_model=NotificationSetting,
    summary="更新通知设置",
)
async def update_settings(
    user: CurrentUser,
    body: Annotated[UpdateNotificationSettingsDto, Body(UpdateNotificationSettingsDto)],
    context: Context,
    session: DbSession,
) -> dict[str, Any]:
    setting = await _service(context, session).update_settings(
        user.id,
        SettingsInput(
            category=body.category,
            enable_in_app=body.enable_in_app,
            enable_email=body.enable_email,
            enable_sms=body.enable_sms,
            enable_push=body.enable_push,
        ),
    )
    return serialize_setting(setting)


@router.endpoint(
    "POST",
    "/{id}/read",
    operation_id="markNotificationAsRead",
    auth=Auth.JWT,
    response_model=Notification,
    summary="标记通知为已读",
)
async def mark_as_read(
    user: CurrentUser, id: IdParam, context: Context, session: DbSession
) -> dict[str, Any]:
    return serialize_notification(await _service(context, session).mark_as_read(id, user.id))


@router.endpoint(
    "DELETE",
    "/{id}",
    operation_id="deleteNotification",
    auth=Auth.JWT,
    response_model=None,
    summary="删除通知",
)
async def delete_notification(
    user: CurrentUser, id: IdParam, context: Context, session: DbSession
) -> None:
    await _service(context, session).remove(id, user.id)
