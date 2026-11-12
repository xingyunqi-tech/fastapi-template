from typing import Annotated, Any

from fastapi import Request
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.client import client_ip
from app.common.context import AppContext, Context
from app.common.params import Body
from app.common.routing import ApiRouter, Auth
from app.contract.schemas import AgreeConsentDto, ConsentStatusDto
from app.core.auth.deps import CurrentUser
from app.infrastructure.database.session import DbSession
from app.modules.config.service import AppConfigService
from app.modules.consents.service import ConsentsService

router = ApiRouter(prefix="/v1/me/consents", tags=["Consents"])


def _service(context: AppContext, session: AsyncSession) -> ConsentsService:
    return ConsentsService(session, AppConfigService(session, context.cache))


@router.endpoint(
    "GET",
    "",
    operation_id="getMyConsents",
    auth=Auth.JWT,
    response_model=list[ConsentStatusDto],
    summary="各协议当前版本与同意状态",
)
async def get_my_consents(
    user: CurrentUser, context: Context, session: DbSession
) -> list[dict[str, Any]]:
    return await _service(context, session).find_status(user.id)


@router.endpoint(
    "POST",
    "",
    operation_id="agreeConsent",
    auth=Auth.JWT,
    response_model=ConsentStatusDto,
    summary="同意协议的当前版本，重复同意同一版本保留首次记录",
)
async def agree_consent(
    user: CurrentUser,
    body: Annotated[AgreeConsentDto, Body(AgreeConsentDto)],
    request: Request,
    context: Context,
    session: DbSession,
) -> dict[str, Any]:
    ip = client_ip(request, context.settings.app.trust_proxy)
    return await _service(context, session).agree(user.id, str(body.type_), body.version, ip)
