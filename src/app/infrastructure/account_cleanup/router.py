from app.common.context import Context
from app.common.exceptions import not_found, validation_error
from app.common.params import IdParam
from app.common.routing import ApiRouter, Auth
from app.core.permissions.service import Permission
from app.core.users.repository import UserRepository
from app.infrastructure.account_cleanup import AccountCleanupProducer
from app.infrastructure.database.session import DbSession

router = ApiRouter(prefix="/v1", tags=["Admin Account Cleanup"], rbac=True)


@router.endpoint(
    "POST",
    "/admin/users/{id}/cleanup",
    operation_id="adminRetriggerAccountCleanup",
    auth=Auth.JWT,
    permissions=[Permission.USER_DELETE],
    summary="重新触发已注销用户的数据清理；已有清理任务在排队或执行时不重复入队",
)
async def retrigger(id: IdParam, context: Context, session: DbSession) -> None:
    users = UserRepository(session)
    if await users.find_deleted_by_id(id) is None:
        if await users.find_by_id(id) is not None:
            raise validation_error("user", "userNotDeleted")
        raise not_found({"error": "userNotFound"})
    await AccountCleanupProducer(context).enqueue_cleanup(id)
