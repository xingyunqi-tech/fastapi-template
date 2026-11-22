"""用应用自身的签名器签发访问令牌，供不经登录流程的用例使用。"""

import time
from uuid import UUID, uuid7

lazy from fastapi import FastAPI


def access_token(
    app: FastAPI,
    user_id: UUID,
    *,
    sid: UUID | None = None,
    roles: tuple[str, ...] = ("user",),
) -> str:
    token: str = app.state.context.signer.sign(
        sub=user_id,
        sid=sid or uuid7(),
        roles=roles,
        email_verified=True,
        auth_time=int(time.time()),
    )
    return token
