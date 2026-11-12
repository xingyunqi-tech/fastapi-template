from typing import Final

from sqlalchemy import exists, select, text
from sqlalchemy.dialects.postgresql import distinct_on
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.consents.models import UserConsentModel

_INSERT_IF_ABSENT: Final = text(
    'INSERT INTO "user_consent" ("userId", "type", "version", "ip") '
    "VALUES (:user_id, :type, :version, :ip) "
    'ON CONFLICT ("userId", "type", "version") DO NOTHING'
)


class UserConsentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert_if_absent(
        self, user_id: int, type_: str, version: str, ip: str | None
    ) -> None:
        await self.session.execute(
            _INSERT_IF_ABSENT, {"user_id": user_id, "type": type_, "version": version, "ip": ip}
        )

    async def find_latest_by_user(self, user_id: int) -> list[UserConsentModel]:
        """每种协议最近一次同意的记录。"""
        statement = (
            select(UserConsentModel)
            .ext(distinct_on(UserConsentModel.type))
            .where(UserConsentModel.user_id == user_id)
            .order_by(
                UserConsentModel.type,
                UserConsentModel.agreed_at.desc(),
                UserConsentModel.id.desc(),
            )
        )
        return list(await self.session.scalars(statement))

    async def exists(self, user_id: int, type_: str, version: str) -> bool:
        return bool(
            await self.session.scalar(
                select(
                    exists().where(
                        UserConsentModel.user_id == user_id,
                        UserConsentModel.type == type_,
                        UserConsentModel.version == version,
                    )
                )
            )
        )

    async def find_one(self, user_id: int, type_: str, version: str) -> UserConsentModel | None:
        return await self.session.scalar(
            select(UserConsentModel).where(
                UserConsentModel.user_id == user_id,
                UserConsentModel.type == type_,
                UserConsentModel.version == version,
            )
        )
