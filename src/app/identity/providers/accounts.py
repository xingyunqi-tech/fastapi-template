"""第三方身份与账号的对应：`(provider, openid)` 或微信 `union_id` 任一命中即为同一账号。"""

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.identity.providers import WECHAT_PROVIDERS, ProviderIdentity
from app.identity.providers.models import UserIdentityModel
from app.identity.users.repository import UserRepository

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.identity.providers import ProviderId
    from app.identity.users.models import UserModel


class ProviderAccounts:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def _by_openid(self, provider: ProviderId, openid: str) -> UserIdentityModel | None:
        return await self.session.scalar(
            select(UserIdentityModel).where(
                UserIdentityModel.provider == provider.value, UserIdentityModel.openid == openid
            )
        )

    async def _by_union_id(self, union_id: str) -> UserIdentityModel | None:
        return await self.session.scalar(
            select(UserIdentityModel)
            .where(
                UserIdentityModel.provider.in_([p.value for p in WECHAT_PROVIDERS]),
                UserIdentityModel.union_id == union_id,
            )
            .order_by(UserIdentityModel.created_at)
            .limit(1)
        )

    async def _find(self, provider: ProviderId, identity: ProviderIdentity) -> UserModel | None:
        row = await self._by_openid(provider, identity.openid)
        if row is not None:
            if row.union_id is None and identity.union_id is not None:
                row.union_id = identity.union_id
            return await self.users.find_by_id(row.user_id)
        if identity.union_id is None or provider not in WECHAT_PROVIDERS:
            return None
        linked = await self._by_union_id(identity.union_id)
        if linked is None:
            return None
        await self._add(linked.user_id, provider, identity)
        return await self.users.find_by_id(linked.user_id)

    async def _add(self, user_id: UUID, provider: ProviderId, identity: ProviderIdentity) -> None:
        self.session.add(
            UserIdentityModel(
                user_id=user_id,
                provider=provider.value,
                openid=identity.openid,
                union_id=identity.union_id,
            )
        )
        await self.session.flush()

    async def _find_or_create(
        self, provider: ProviderId, identity: ProviderIdentity
    ) -> UserModel | None:
        user = await self._find(provider, identity)
        if user is not None:
            return user
        user = await self.users.create(
            email=None, phone=None, password_hash=None, display_name=None
        )
        await self._add(user.id, provider, identity)
        return user

    async def resolve(self, provider: ProviderId, identity: ProviderIdentity) -> UserModel | None:
        """找不到时新建账号；并发首次登录（含经 `union_id` 关联）冲突时回读已写入的身份。"""
        try:
            async with self.session.begin_nested():
                return await self._find_or_create(provider, identity)
        except IntegrityError:
            return await self._find(provider, identity)
