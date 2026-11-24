from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path, Query
from pydantic import StringConstraints

from app.common.context import Context
from app.common.pagination import PageParams, SortField, sort_param
from app.common.routing import ApiRouter, Auth
from app.identity.account_deletion.service import delete_account
from app.identity.users.models import UserStatus
from app.identity.users.repository import SORTABLE
from app.identity.users.schemas import (
    CreateUserRequest,
    PermissionInfo,
    Role,
    SetUserRolesRequest,
    UpdateUserRequest,
    User,
    UserPage,
)
from app.identity.users.service import UsersService
from app.infrastructure.audit import Audit
from app.infrastructure.database.session import DbSession
from app.security import rbac
from app.security.rbac import Permission

router = ApiRouter(prefix="/v1", tags=["Users"])

UserId = Annotated[UUID, Path()]
UserSort = Annotated[list[SortField], Depends(sort_param(tuple(SORTABLE), "-createdAt"))]
Keyword = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


@router.endpoint(
    "GET",
    "/users",
    operation_id="listUsers",
    auth=Auth.JWT,
    permissions=[Permission.USER_READ],
    summary="用户列表",
)
async def list_users(
    page: Annotated[PageParams, Query()],
    sort: UserSort,
    context: Context,
    session: DbSession,
    keyword: Annotated[Keyword | None, Query()] = None,
    status: Annotated[UserStatus | None, Query()] = None,
    role: Annotated[rbac.Role | None, Query()] = None,
) -> UserPage:
    users, total = await UsersService(context, session).find_page(
        keyword=keyword or None,
        status=status,
        role=role,
        sort=sort,
        offset=page.offset,
        limit=page.page_size,
    )
    return UserPage.of([User.from_model(u, context.urls) for u in users], total, page)


@router.endpoint(
    "POST",
    "/users",
    operation_id="createUser",
    audit=Audit("user.create", resource="user"),
    auth=Auth.JWT,
    status=201,
    permissions=[Permission.USER_WRITE],
    summary="创建用户",
)
async def create_user(body: CreateUserRequest, context: Context, session: DbSession) -> User:
    user = await UsersService(context, session).create(body)
    return User.from_model(user, context.urls)


@router.endpoint(
    "GET",
    "/users/{id}",
    operation_id="getUser",
    auth=Auth.JWT,
    permissions=[Permission.USER_READ],
    summary="用户详情",
)
async def get_user(id: UserId, context: Context, session: DbSession) -> User:
    return User.from_model(await UsersService(context, session).get(id), context.urls)


@router.endpoint(
    "PATCH",
    "/users/{id}",
    operation_id="updateUser",
    audit=Audit("user.update", resource="user"),
    auth=Auth.JWT,
    permissions=[Permission.USER_WRITE],
    summary="更新用户",
)
async def update_user(
    id: UserId, body: UpdateUserRequest, context: Context, session: DbSession
) -> User:
    user = await UsersService(context, session).update(id, body)
    return User.from_model(user, context.urls)


@router.endpoint(
    "DELETE",
    "/users/{id}",
    operation_id="deleteUser",
    audit=Audit("user.delete", resource="user"),
    auth=Auth.JWT,
    status=202,
    permissions=[Permission.USER_WRITE],
    summary="删除用户",
)
async def delete_user(id: UserId, context: Context, session: DbSession) -> None:
    user = await UsersService(context, session).get(id)
    await delete_account(context, session, user)


@router.endpoint(
    "PUT",
    "/users/{id}/roles",
    operation_id="setUserRoles",
    audit=Audit("user.setRoles", resource="user"),
    auth=Auth.JWT,
    permissions=[Permission.ROLE_WRITE],
    summary="设置用户角色",
)
async def set_user_roles(
    id: UserId, body: SetUserRolesRequest, context: Context, session: DbSession
) -> User:
    user = await UsersService(context, session).set_roles(id, body)
    return User.from_model(user, context.urls)


@router.endpoint(
    "GET",
    "/roles",
    operation_id="listRoles",
    auth=Auth.JWT,
    permissions=[Permission.ROLE_READ],
    summary="角色列表",
)
async def list_roles() -> list[Role]:
    return [
        Role(code=role.value, name=rbac.ROLE_NAMES[role], permissions=rbac.permissions_of(role))
        for role in rbac.Role
    ]


@router.endpoint(
    "GET",
    "/permissions",
    operation_id="listPermissions",
    auth=Auth.JWT,
    permissions=[Permission.ROLE_READ],
    summary="权限列表",
)
async def list_permissions() -> list[PermissionInfo]:
    return [PermissionInfo(code=code) for code in rbac.permission_codes()]
