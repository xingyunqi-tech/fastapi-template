"""协议同意记录（nestjs ConsentsService）：当前版本取自配置项，重复同意同一版本保留首次记录。"""

from typing import Any, Final

lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import validation_error
from app.common.responses import iso_timestamp
from app.common.validation.js import trim
from app.contract.schemas import ConsentType
from app.modules.consents.repository import UserConsentRepository
lazy from app.modules.config.service import AppConfigService
lazy from app.modules.consents.models import UserConsentModel

VERSION_KEYS: Final = {
    ConsentType.PRIVACY_POLICY: "consent.privacyPolicyVersion",
    ConsentType.USER_AGREEMENT: "consent.userAgreementVersion",
    ConsentType.AI_SERVICE: "consent.aiServiceVersion",
}

type ConsentStatus = dict[str, Any]


def _status(
    type_: str, current_version: str | None, latest: UserConsentModel | None, agreed: bool
) -> ConsentStatus:
    return {
        "type": str(type_),
        "currentVersion": current_version,
        "agreedVersion": latest.version if latest else None,
        "agreedAt": iso_timestamp(latest.agreed_at) if latest else None,
        "agreed": agreed,
        "needsConsent": current_version is not None and not agreed,
    }


class ConsentsService:
    def __init__(self, session: AsyncSession, config: AppConfigService) -> None:
        self.repository = UserConsentRepository(session)
        self.config = config

    async def find_status(self, user_id: int) -> list[ConsentStatus]:
        versions = [await self._current_version(type_) for type_ in ConsentType]
        latest = {
            consent.type: consent for consent in await self.repository.find_latest_by_user(user_id)
        }
        statuses: list[ConsentStatus] = []
        for type_, version in zip(ConsentType, versions, strict=True):
            consent = latest.get(type_)
            agreed = await self._agreed_current(user_id, type_, version, consent)
            statuses.append(_status(type_, version, consent, agreed))
        return statuses

    async def agree(self, user_id: int, type_: str, version: str, ip: str | None) -> ConsentStatus:
        current = await self._current_version(ConsentType(type_))
        if current is None or version != current:
            raise validation_error("version", "consentVersionNotCurrent")
        await self.repository.insert_if_absent(user_id, type_, version, ip)
        agreed = await self.repository.find_one(user_id, type_, version)
        return _status(type_, current, agreed, True)

    async def has_agreed_current(self, user_id: int, type_: ConsentType) -> bool:
        """已同意该协议的当前版本；未配置当前版本时为 False。"""
        current = await self._current_version(type_)
        return current is not None and await self.repository.exists(user_id, type_, current)

    async def has_agreed_ai_service(self, user_id: int) -> bool:
        return await self.has_agreed_current(user_id, ConsentType.AI_SERVICE)

    async def _current_version(self, type_: ConsentType) -> str | None:
        value = await self.config.get_value(VERSION_KEYS[type_])
        return value if isinstance(value, str) and trim(value) != "" else None

    async def _agreed_current(
        self,
        user_id: int,
        type_: str,
        current: str | None,
        latest: UserConsentModel | None,
    ) -> bool:
        if current is None or latest is None:
            return False
        return latest.version == current or await self.repository.exists(user_id, type_, current)
