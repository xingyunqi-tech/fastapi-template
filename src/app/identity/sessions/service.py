"""会话与令牌：登录签发、refresh 轮换（30 秒宽限期、重用吊销）、会话吊销与活跃时间。"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
lazy from collections.abc import Sequence
lazy from uuid import UUID

from cryptography.exceptions import InvalidTag
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import AppError
from app.identity.sessions.grace import hash_token, new_refresh_token, seal, unseal
from app.identity.sessions.repository import SessionRepository
from app.identity.users.models import UserStatus
from app.identity.users.repository import UserRepository
from app.infrastructure.throttler import session_subject
from app.infrastructure.throttler.rules import AUTH_REFRESH
lazy from app.common.client import ClientInfo
lazy from app.common.context import AppContext
lazy from app.identity.sessions.models import SessionModel
lazy from app.identity.users.models import UserModel

logger = logging.getLogger("app.auth")

GRACE_PERIOD: Final = timedelta(seconds=30)
TOUCH_INTERVAL: Final = timedelta(minutes=5)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class Login:
    tokens: IssuedTokens
    user: UserModel


def can_sign_in(user: UserModel | None) -> bool:
    return user is not None and user.deleted_at is None and user.status == UserStatus.ACTIVE


class SessionService:
    def __init__(self, context: AppContext, session: AsyncSession) -> None:
        self.context = context
        self.repository = SessionRepository(session)
        self.users = UserRepository(session)

    def _access_token(self, user: UserModel, record: SessionModel) -> str:
        return self.context.signer.sign(
            sub=user.id,
            sid=record.id,
            roles=user.roles,
            email_verified=user.email_verified_at is not None,
            auth_time=int(record.auth_time.timestamp()),
        )

    async def _issue(self, user: UserModel, record: SessionModel) -> tuple[IssuedTokens, str]:
        refresh_token = new_refresh_token()
        await self.repository.add_refresh_token(record.id, hash_token(refresh_token))
        tokens = IssuedTokens(
            self._access_token(user, record), refresh_token, self.context.signer.expires_in
        )
        return tokens, refresh_token

    async def sign_in(self, user: UserModel, client: ClientInfo) -> Login:
        """新建会话，有效期自登录起 `AUTH_REFRESH_TTL`；记录最近登录时间。"""
        now = utcnow()
        record = await self.repository.create(
            user_id=user.id,
            user_agent=client.user_agent,
            ip=client.ip,
            auth_time=now,
            expires_at=now + self.context.settings.auth.refresh_ttl,
        )
        user.last_login_at = now
        tokens, _ = await self._issue(user, record)
        return Login(tokens, await self.users.reload(user))

    async def refresh(self, refresh_token: str, ip_subject: str) -> Login:
        """约定 §5.2；会话行加锁，并发刷新只产生一对新令牌；`auth-refresh` 查不到会话时按 IP。"""
        row = await self.repository.find_refresh_token(hash_token(refresh_token))
        subject = session_subject(row.session_id) if row is not None else ip_subject
        await self.context.rate_limiter.check(AUTH_REFRESH, subject)
        if row is None:
            raise AppError("auth.refreshTokenInvalid")
        now = utcnow()
        record = await self.repository.lock(row.session_id)
        row = await self.repository.reload_refresh_token(row)
        if record is None or record.revoked_at is not None or record.expires_at <= now:
            raise AppError("auth.refreshTokenInvalid")
        user = await self.users.find_by_id(record.user_id)
        if user is None or not can_sign_in(user):
            raise AppError("auth.refreshTokenInvalid")
        if row.rotated_at is None:
            tokens, _ = await self._issue(user, record)
            row.rotated_at = now
            row.grace_ciphertext = seal(
                refresh_token,
                {
                    "accessToken": tokens.access_token,
                    "refreshToken": tokens.refresh_token,
                    "expiresIn": tokens.expires_in,
                },
            )
            return Login(tokens, user)
        if now - row.rotated_at <= GRACE_PERIOD and row.grace_ciphertext is not None:
            try:
                pair = unseal(refresh_token, row.grace_ciphertext)
            except InvalidTag:
                raise AppError("auth.refreshTokenInvalid") from None
            return Login(
                IssuedTokens(pair["accessToken"], pair["refreshToken"], int(pair["expiresIn"])),
                user,
            )
        logger.warning("Refresh token reused, session %s revoked", record.id)
        await self.revoke(record.user_id, only=[record.id])
        raise AppError("auth.refreshTokenReused")

    async def revoke(
        self, user_id: UUID, *, only: Sequence[UUID] = (), keep: UUID | None = None
    ) -> list[UUID]:
        """标记吊销并写入约定 §5.1 的吊销键。"""
        revoked = await self.repository.revoke(user_id=user_id, now=utcnow(), only=only, keep=keep)
        await self.context.revocations.revoke(revoked)
        return revoked

    async def list_active(self, user_id: UUID) -> list[SessionModel]:
        return await self.repository.list_active(user_id, utcnow())

    async def revoke_one(self, user_id: UUID, session_id: UUID) -> None:
        if await self.repository.find_active(session_id, user_id, utcnow()) is None:
            raise AppError("auth.sessionNotFound")
        await self.revoke(user_id, only=[session_id])

    async def touch(self, session_id: UUID) -> None:
        now = utcnow()
        await self.repository.touch(session_id, now, now - TOUCH_INTERVAL)
