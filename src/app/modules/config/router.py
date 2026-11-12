from typing import Annotated, Any

from fastapi import Path

from app.common.context import Context
from app.common.params import Body
from app.common.routing import ApiRouter, Auth
from app.contract.schemas import Config, UpsertConfigDto
from app.core.permissions.service import Permission
from app.infrastructure.database.session import DbSession
from app.modules.config.service import AppConfigService

router = ApiRouter(prefix="/v1/config", tags=["Config"])

KeyParam = Annotated[str, Path()]


@router.endpoint(
    "GET",
    "/{key}",
    operation_id="getConfig",
    auth=Auth.PUBLIC,
    response_model=Config,
    summary="获取公开配置",
)
async def get_config(key: KeyParam, context: Context, session: DbSession) -> dict[str, Any]:
    return await AppConfigService(session, context.cache).find_public_by_key(key)


@router.endpoint(
    "GET",
    "/admin/{key}",
    operation_id="getConfigAsAdmin",
    auth=Auth.JWT,
    permissions=[Permission.SYSTEM_CONFIG],
    response_model=Config,
    summary="获取配置（管理员）",
)
async def get_config_as_admin(
    key: KeyParam, context: Context, session: DbSession
) -> dict[str, Any]:
    return await AppConfigService(session, context.cache).find_by_key(key)


@router.endpoint(
    "PUT",
    "/{key}",
    operation_id="upsertConfig",
    auth=Auth.JWT,
    permissions=[Permission.SYSTEM_CONFIG],
    response_model=Config,
    summary="写入配置（管理员）",
)
async def upsert_config(
    key: KeyParam,
    body: Annotated[UpsertConfigDto, Body(UpsertConfigDto)],
    context: Context,
    session: DbSession,
) -> dict[str, Any]:
    is_public = body.is_public if "is_public" in body.model_fields_set else None
    return await AppConfigService(session, context.cache).upsert_generic(key, body.value, is_public)
