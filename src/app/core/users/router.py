from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Path, Query

from app.common.context import AppContext, Context
from app.common.errors import AppError
from app.common.pagination import PageParams, SortField, sort_param
from app.common.routing import ApiRouter, Auth
from app.common.types import SerialId
from app.core.permissions.service import Permission
from app.core.session.repository import SessionRepository
from app.core.session.service import SessionService
from app.core.users.repository import SORTABLE, UserRepository
from app.core.users.schemas import CreateUserRequest, UpdateUserRequest, User, UserPage
from app.core.users.service import UsersService, user_input_from
from app.infrastructure.account_cleanup import AccountCleanupProducer
from app.infrastructure.database.session import DbSession
from app.modules.files.service import FilesService

router = ApiRouter(prefix="/v1/users", tags=["Users"], rbac=True)

UserId = Annotated[UUID, Path()]
UserSort = Annotated[list[SortField], Depends(sort_param(tuple(SORTABLE)))]


def build_users_service(context: AppContext, session: Any) -> UsersService:
    return UsersService(
        UserRepository(session),
        FilesService(session),
        SessionService(SessionRepository(session), context.cache),
        context.passwords,
        AccountCleanupProducer(context).after_commit(session),
    )


@router.endpoint(
    "POST",
    "",
    operation_id="createUser",
    auth=Auth.JWT,
    status=201,
    permissions=[Permission.USER_CREATE],
    summary="创建用户（管理员）",
)
async def create_user(body: CreateUserRequest, context: Context, session: DbSession) -> User:
    user = await build_users_service(context, session).create(user_input_from(body))
    return User.from_model(user, context.file_urls)


@router.endpoint(
    "GET",
    "",
    operation_id="listUsers",
    auth=Auth.JWT,
    permissions=[Permission.USER_LIST],
    summary="获取用户列表（管理员）",
)
async def list_users(
    page: Annotated[PageParams, Query()],
    sort: UserSort,
    context: Context,
    session: DbSession,
    role_id: Annotated[list[SerialId] | None, Query(alias="roleId")] = None,
) -> UserPage:
    users, total = await build_users_service(context, session).find_page(
        role_ids=role_id, sort=sort, offset=page.offset, limit=page.page_size
    )
    return UserPage.of([User.from_model(u, context.file_urls) for u in users], total, page)


@router.endpoint(
    "GET",
    "/{id}",
    operation_id="getUser",
    auth=Auth.JWT,
    permissions=[Permission.USER_READ],
    summary="获取用户详情（管理员）",
)
async def get_user(id: UserId, context: Context, session: DbSession) -> User:
    user = await build_users_service(context, session).find_by_id(id)
    if user is None:
        raise AppError("user.notFound")
    return User.from_model(user, context.file_urls)


@router.endpoint(
    "PUT",
    "/{id}",
    operation_id="updateUser",
    auth=Auth.JWT,
    permissions=[Permission.USER_UPDATE],
    summary="更新用户（管理员）",
)
async def update_user(
    id: UserId, body: UpdateUserRequest, context: Context, session: DbSession
) -> User:
    user = await build_users_service(context, session).update_by_admin(id, user_input_from(body))
    return User.from_model(user, context.file_urls)


@router.endpoint(
    "DELETE",
    "/{id}",
    operation_id="deleteUser",
    auth=Auth.JWT,
    permissions=[Permission.USER_DELETE],
    summary="删除用户（管理员）",
)
async def delete_user(id: UserId, context: Context, session: DbSession) -> None:
    await build_users_service(context, session).remove(id)
