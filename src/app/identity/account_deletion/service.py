"""注销（`deleteMe` / `deleteUser`）：软删用户、吊销会话；提交后入队清理并发布 `user.deleted`。"""

from typing import TYPE_CHECKING

from sqlalchemy.dialects.postgresql import insert

from app.identity.account_deletion.cleanup import AccountCleanupProducer
from app.identity.account_deletion.models import AccountDeletionModel
from app.identity.sessions.service import SessionService, utcnow
from app.identity.users.repository import UserRepository
from app.identity.users.service import ensure_not_last_admin
from app.infrastructure.database.session import after_commit
from app.infrastructure.events import publish, user_deleted

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.context import AppContext
    from app.identity.users.models import UserModel


async def delete_account(context: AppContext, session: AsyncSession, user: UserModel) -> None:
    users = UserRepository(session)
    await ensure_not_last_admin(users, user)
    await SessionService(context, session).revoke(user.id)
    await users.soft_delete(user, utcnow())
    await session.execute(
        insert(AccountDeletionModel)
        .values(user_id=user.id)
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    producer = AccountCleanupProducer(context)
    user_id = user.id

    event = user_deleted(context.settings.telemetry.service_name, str(user_id))

    async def enqueue() -> None:
        await producer.enqueue_cleanup(user_id)
        await publish(context.events, event)

    after_commit(session, enqueue)
