from typing import Annotated, Any

lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.context import AppContext, Context
from app.common.params import Body
from app.common.routing import ApiRouter, Auth
from app.contract.schemas import AppProfile, UpdateAppProfileDto
from app.core.permissions.service import Permission
from app.infrastructure.database.session import DbSession
from app.modules.app_profile.service import AppProfileService
from app.modules.config.service import AppConfigService

router = ApiRouter(prefix="/v1/app-profile", tags=["App Profile"])
admin_router = ApiRouter(prefix="/v1/admin/app-profile", tags=["Admin App Profile"])


def _service(context: AppContext, session: AsyncSession) -> AppProfileService:
    return AppProfileService(AppConfigService(session, context.cache))


@router.endpoint(
    "GET",
    "",
    operation_id="getAppProfile",
    auth=Auth.PUBLIC,
    response_model=AppProfile,
    summary="小程序站点信息，未配置的字段为 null",
)
async def get_app_profile(context: Context, session: DbSession) -> dict[str, Any]:
    return await _service(context, session).get()


@admin_router.endpoint(
    "PUT",
    "",
    operation_id="adminUpdateAppProfile",
    auth=Auth.JWT,
    permissions=[Permission.SYSTEM_CONFIG],
    response_model=AppProfile,
    summary="保存小程序站点信息：整体覆盖，未传或空白的字段保存为未配置（null）",
)
async def admin_update_app_profile(
    body: Annotated[UpdateAppProfileDto, Body(UpdateAppProfileDto)],
    context: Context,
    session: DbSession,
) -> dict[str, Any]:
    return await _service(context, session).update(body.model_dump(by_alias=True))
