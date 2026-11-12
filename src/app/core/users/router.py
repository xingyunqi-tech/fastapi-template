from typing import Annotated, Any

from app.common.context import AppContext, Context
from app.common.params import Body, IdParam, Query
from app.common.routing import ApiRouter, Auth
from app.contract.schemas import (
    CreateUserDto,
    InfinityPaginationUserResponseDto,
    UpdateUserDto,
    User,
)
from app.core.permissions.service import Permission
from app.core.session.repository import SessionRepository
from app.core.session.service import SessionService
from app.core.users.repository import Sort, UserRepository
from app.core.users.schemas import QueryUserDto
from app.core.users.serializers import View, serialize_user
from app.core.users.service import UsersService, user_input_from
from app.infrastructure.database.session import DbSession
from app.modules.files.service import FilesService

MAX_LIMIT = 50

router = ApiRouter(prefix="/v1/users", tags=["Users"], rbac=True)


async def _no_cleanup(user_id: int) -> None:
    return None


def build_users_service(context: AppContext, session: Any) -> UsersService:
    return UsersService(
        UserRepository(session),
        FilesService(session),
        SessionService(SessionRepository(session), context.cache),
        context.passwords,
        _no_cleanup,
    )


@router.endpoint(
    "POST",
    "",
    operation_id="createUser",
    auth=Auth.JWT,
    permissions=[Permission.USER_CREATE],
    response_model=User,
    summary="创建用户（管理员）",
)
async def create_user(
    body: Annotated[CreateUserDto, Body(CreateUserDto)], context: Context, session: DbSession
) -> dict[str, Any]:
    user, created = await build_users_service(context, session).create(user_input_from(body))
    return serialize_user(user, context.file_urls, view=View.ADMIN, created=created)


@router.endpoint(
    "GET",
    "",
    operation_id="getUsers",
    auth=Auth.JWT,
    permissions=[Permission.USER_LIST],
    response_model=InfinityPaginationUserResponseDto,
    summary="获取用户列表（管理员）",
)
async def get_users(
    query: Annotated[QueryUserDto, Query(QueryUserDto)], context: Context, session: DbSession
) -> dict[str, Any]:
    page = int(query.page) if query.page is not None else 1
    limit = min(int(query.limit) if query.limit is not None else 10, MAX_LIMIT)
    role_ids = (
        [int(r.id) for r in query.filters.roles] if query.filters and query.filters.roles else None
    )
    sort = [Sort(s.order_by, s.order) for s in query.sort] if query.sort else None
    users = await build_users_service(context, session).find_many(
        role_ids=role_ids, sort=sort, page=page, limit=limit
    )
    return {
        "data": [serialize_user(u, context.file_urls, view=View.ADMIN) for u in users],
        "hasNextPage": len(users) == limit,
    }


@router.endpoint(
    "GET",
    "/{id}",
    operation_id="getUser",
    auth=Auth.JWT,
    permissions=[Permission.USER_READ],
    response_model=User,
    summary="获取用户详情（管理员）",
)
async def get_user(id: IdParam, context: Context, session: DbSession) -> dict[str, Any] | None:
    user = await build_users_service(context, session).find_by_id(id)
    return serialize_user(user, context.file_urls, view=View.ADMIN) if user else None


@router.endpoint(
    "PUT",
    "/{id}",
    operation_id="updateUser",
    auth=Auth.JWT,
    permissions=[Permission.USER_UPDATE],
    response_model=User,
    summary="更新用户（管理员）",
)
async def update_user(
    id: IdParam,
    body: Annotated[UpdateUserDto, Body(UpdateUserDto)],
    context: Context,
    session: DbSession,
) -> dict[str, Any] | None:
    user = await build_users_service(context, session).update_by_admin(id, user_input_from(body))
    return serialize_user(user, context.file_urls, view=View.ADMIN)


@router.endpoint(
    "DELETE",
    "/{id}",
    operation_id="deleteUser",
    auth=Auth.JWT,
    permissions=[Permission.USER_DELETE],
    summary="删除用户（管理员）",
)
async def delete_user(id: IdParam, context: Context, session: DbSession) -> None:
    await build_users_service(context, session).remove(id)
