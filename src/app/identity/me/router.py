from typing import Annotated
from uuid import UUID

from fastapi import Path

from app.common.context import Context
from app.common.routing import ApiRouter, Auth, Throttle
from app.identity.me.schemas import (
    BindPhoneRequest,
    BindPhoneWithWechatRequest,
    ChangePasswordRequest,
    UpdateMeRequest,
)
from app.identity.me.service import MeService
from app.identity.sessions.schemas import Session
from app.identity.sessions.service import SessionService
from app.identity.users.schemas import User
from app.infrastructure.database.session import DbSession
from app.security.deps import CurrentPrincipal, RecentPrincipal

router = ApiRouter(prefix="/v1/me", tags=["Me"])

SessionId = Annotated[UUID, Path()]


@router.endpoint("GET", "", operation_id="getMe", auth=Auth.JWT, summary="当前用户")
async def get_me(principal: CurrentPrincipal, context: Context, session: DbSession) -> User:
    return User.from_model(await MeService(context, session).get(principal), context.file_urls)


@router.endpoint("PATCH", "", operation_id="updateMe", auth=Auth.JWT, summary="更新当前用户")
async def update_me(
    principal: CurrentPrincipal, body: UpdateMeRequest, context: Context, session: DbSession
) -> User:
    user = await MeService(context, session).update(principal, body)
    return User.from_model(user, context.file_urls)


@router.endpoint(
    "DELETE", "", operation_id="deleteMe", auth=Auth.JWT, status=202, summary="注销账号"
)
async def delete_me(principal: RecentPrincipal, context: Context, session: DbSession) -> None:
    await MeService(context, session).delete(principal)


@router.endpoint(
    "PUT",
    "/password",
    operation_id="changePassword",
    auth=Auth.JWT,
    throttle=Throttle(limit=5, ttl=900_000),
    summary="修改密码",
)
async def change_password(
    principal: CurrentPrincipal,
    body: ChangePasswordRequest,
    context: Context,
    session: DbSession,
) -> None:
    await MeService(context, session).change_password(principal, body)


@router.endpoint("PUT", "/phone", operation_id="bindPhone", auth=Auth.JWT, summary="绑定手机号")
async def bind_phone(
    principal: CurrentPrincipal, body: BindPhoneRequest, context: Context, session: DbSession
) -> User:
    user = await MeService(context, session).bind_phone(principal, body)
    return User.from_model(user, context.file_urls)


@router.endpoint(
    "PUT",
    "/phone/wechat",
    operation_id="bindPhoneWithWechat",
    auth=Auth.JWT,
    summary="用微信手机号绑定",
)
async def bind_phone_with_wechat(
    principal: CurrentPrincipal,
    body: BindPhoneWithWechatRequest,
    context: Context,
    session: DbSession,
) -> User:
    user = await MeService(context, session).bind_phone_with_wechat(principal, body.code)
    return User.from_model(user, context.file_urls)


@router.endpoint(
    "GET", "/sessions", operation_id="listMySessions", auth=Auth.JWT, summary="我的会话"
)
async def list_my_sessions(
    principal: CurrentPrincipal, context: Context, session: DbSession
) -> list[Session]:
    records = await MeService(context, session).sessions_of(principal)
    return [
        Session(
            id=record.id,
            user_agent=record.user_agent,
            ip=str(record.ip) if record.ip is not None else None,
            created_at=record.created_at,
            last_active_at=record.last_active_at,
            current=record.id == principal.sid,
        )
        for record in records
    ]


@router.endpoint(
    "DELETE",
    "/sessions/{id}",
    operation_id="revokeMySession",
    auth=Auth.JWT,
    summary="吊销指定会话",
)
async def revoke_my_session(
    principal: CurrentPrincipal, id: SessionId, context: Context, session: DbSession
) -> None:
    await SessionService(context, session).revoke_one(principal.sub, id)


@router.endpoint(
    "POST",
    "/sessions/revoke-others",
    operation_id="revokeOtherSessions",
    auth=Auth.JWT,
    summary="吊销其他会话",
)
async def revoke_other_sessions(
    principal: CurrentPrincipal, context: Context, session: DbSession
) -> None:
    await SessionService(context, session).revoke(principal.sub, keep=principal.sid)
