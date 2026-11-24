"""签发方的 JWT 鉴权：在 `security` 校验之上更新会话活跃时间（每会话最多 5 分钟一次）。"""

import logging
from typing import Final

from app.common.context import Context
from app.identity.sessions.service import TOUCH_INTERVAL, SessionService
from app.infrastructure.database.session import DbSession
from app.security.deps import CurrentPrincipal
from app.security.jwt import Principal

logger = logging.getLogger("app.auth")
TOUCH_MARK_TTL_MS: Final = int(TOUCH_INTERVAL.total_seconds() * 1000)


def touch_mark_key(session_id: object) -> str:
    return f"session:touched:{session_id}"


async def touched_principal(
    principal: CurrentPrincipal, context: Context, session: DbSession
) -> Principal:
    """在请求事务的 SAVEPOINT 中更新 `last_active_at`；失败只记日志。"""
    key = touch_mark_key(principal.sid)
    if await context.cache.get(key) is not None:
        return principal
    try:
        async with session.begin_nested():
            await SessionService(context, session).touch(principal.sid)
        await context.cache.set(key, 1, TOUCH_MARK_TTL_MS)
    except Exception as error:
        logger.warning("Failed to update lastActiveAt of session %s: %s", principal.sid, error)
    return principal
