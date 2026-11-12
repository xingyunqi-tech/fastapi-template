from typing import Annotated, Any, Final

from pydantic import BaseModel
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.context import AppContext, Context
from app.common.pagination import pagination
from app.common.params import Body, IdParam, Query
from app.common.routing import ApiRouter, Auth
from app.contract.schemas import (
    Address,
    CreateAddressDto,
    PaginationAddressResponseDto,
    UpdateAddressDto,
)
from app.core.auth.deps import CurrentUser
from app.infrastructure.database.session import DbSession
from app.modules.addresses.repository import AddressRepository, Sort
from app.modules.addresses.schemas import QueryAddressDto
from app.modules.addresses.serializers import serialize_address
from app.modules.addresses.service import AddressesService
from app.modules.regions.service import RegionsService

MAX_LIMIT: Final = 50

router = ApiRouter(prefix="/v1/addresses", tags=["Addresses"])


def build_addresses_service(session: AsyncSession, context: AppContext) -> AddressesService:
    return AddressesService(AddressRepository(session), RegionsService(session, context.cache))


def _provided(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(by_alias=True, exclude_unset=True)


@router.endpoint(
    "POST",
    "",
    operation_id="createAddress",
    auth=Auth.JWT,
    response_model=Address,
    summary="创建地址",
)
async def create_address(
    body: Annotated[CreateAddressDto, Body(CreateAddressDto)],
    user: CurrentUser,
    session: DbSession,
    context: Context,
) -> dict[str, Any]:
    address = await build_addresses_service(session, context).create(user.id, _provided(body))
    return serialize_address(address)


@router.endpoint(
    "GET",
    "",
    operation_id="getAddresses",
    auth=Auth.JWT,
    response_model=PaginationAddressResponseDto,
    summary="获取地址列表",
)
async def get_addresses(
    query: Annotated[QueryAddressDto, Query(QueryAddressDto)],
    user: CurrentUser,
    session: DbSession,
    context: Context,
) -> dict[str, Any]:
    page = query.page if query.page is not None else 1
    limit = min(query.limit if query.limit is not None else 10, MAX_LIMIT)
    filters = _provided(query.filters) if isinstance(query.filters, BaseModel) else None
    sort = (
        [Sort(item.order_by, item.order) for item in query.sort]
        if isinstance(query.sort, list)
        else None
    )
    data, total = await build_addresses_service(session, context).find_page(
        user.id, filters=filters, sort=sort, page=page, limit=limit
    )
    return pagination([serialize_address(a) for a in data], total, page, limit)


@router.endpoint(
    "GET",
    "/default",
    operation_id="getDefaultAddress",
    auth=Auth.JWT,
    response_model=Address,
    summary="获取默认地址",
)
async def get_default_address(
    user: CurrentUser, session: DbSession, context: Context
) -> dict[str, Any] | None:
    address = await build_addresses_service(session, context).find_default(user.id)
    return serialize_address(address) if address is not None else None


@router.endpoint(
    "GET",
    "/{id}",
    operation_id="getAddress",
    auth=Auth.JWT,
    response_model=Address,
    summary="获取地址详情",
)
async def get_address(
    id: IdParam, user: CurrentUser, session: DbSession, context: Context
) -> dict[str, Any]:
    return serialize_address(
        await build_addresses_service(session, context).find_by_id(id, user.id)
    )


@router.endpoint(
    "PUT",
    "/{id}",
    operation_id="updateAddress",
    auth=Auth.JWT,
    response_model=Address,
    summary="更新地址",
)
async def update_address(
    id: IdParam,
    body: Annotated[UpdateAddressDto, Body(UpdateAddressDto)],
    user: CurrentUser,
    session: DbSession,
    context: Context,
) -> dict[str, Any]:
    address = await build_addresses_service(session, context).update(id, user.id, _provided(body))
    return serialize_address(address)


@router.endpoint(
    "PUT",
    "/{id}/default",
    operation_id="setDefaultAddress",
    auth=Auth.JWT,
    response_model=Address,
    summary="设为默认地址",
)
async def set_default_address(
    id: IdParam, user: CurrentUser, session: DbSession, context: Context
) -> dict[str, Any]:
    return serialize_address(
        await build_addresses_service(session, context).set_default(id, user.id)
    )


@router.endpoint(
    "DELETE",
    "/{id}",
    operation_id="deleteAddress",
    auth=Auth.JWT,
    summary="删除地址",
)
async def delete_address(
    id: IdParam, user: CurrentUser, session: DbSession, context: Context
) -> None:
    await build_addresses_service(session, context).remove(id, user.id)
