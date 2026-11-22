"""一次性令牌（约定 §11.1）：只存 SHA-256，（user_id, purpose）唯一，新令牌覆盖旧令牌。"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Final
lazy from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.auth.models import TokenPurpose, VerificationTokenModel
from app.identity.sessions.grace import hash_token

TOKEN_TTL: Final = {
    TokenPurpose.VERIFY_EMAIL: timedelta(hours=24),
    TokenPurpose.RESET_PASSWORD: timedelta(minutes=30),
}
TOKEN_BYTES: Final = 32


def password_fingerprint(password_hash: str | None) -> str:
    return hashlib.sha256((password_hash or "").encode()).hexdigest()


class VerificationTokens:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(
        self, user_id: UUID, purpose: TokenPurpose, *, fingerprint: str | None = None
    ) -> str:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        values = {
            "user_id": user_id,
            "purpose": purpose.value,
            "token_hash": hash_token(token),
            "password_fingerprint": fingerprint,
            "expires_at": datetime.now(UTC) + TOKEN_TTL[purpose],
        }
        statement = insert(VerificationTokenModel).values(values)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["user_id", "purpose"],
                set_={key: statement.excluded[key] for key in values if key != "user_id"},
            )
        )
        return token

    async def consume(self, token: str, purpose: TokenPurpose) -> VerificationTokenModel | None:
        """未过期则删除并返回记录；无效或过期返回 None。"""
        row = await self.session.scalar(
            select(VerificationTokenModel)
            .where(
                VerificationTokenModel.token_hash == hash_token(token),
                VerificationTokenModel.purpose == purpose.value,
            )
            .with_for_update()
        )
        if row is None:
            return None
        await self.session.execute(
            delete(VerificationTokenModel).where(VerificationTokenModel.id == row.id)
        )
        if row.expires_at <= datetime.now(UTC):
            return None
        return row

    async def revoke(self, user_id: UUID, purpose: TokenPurpose) -> None:
        await self.session.execute(
            delete(VerificationTokenModel).where(
                VerificationTokenModel.user_id == user_id,
                VerificationTokenModel.purpose == purpose.value,
            )
        )
