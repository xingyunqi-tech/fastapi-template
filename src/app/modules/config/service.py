"""远程配置（nestjs AppConfigService）：缓存键带写入版本号，写入后读取新版本号对应的缓存。"""

import logging
import uuid
from typing import Any, Final

lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import not_found, unprocessable
from app.common.responses import iso_timestamp
from app.infrastructure.database.session import after_commit
from app.modules.config.definitions import find_config_definition
from app.modules.config.repository import ConfigRepository
lazy from app.infrastructure.cache import Cache
lazy from app.modules.config.models import ConfigModel

logger = logging.getLogger("app.config")

CONFIG_CACHE_TTL_MS: Final = 60_000
CONFIG_VERSION_TTL_MS: Final = 24 * 60 * 60 * 1000

type ConfigView = dict[str, Any]


def config_version_key(key: str) -> str:
    return f"config:version:{key}"


def config_cache_key(key: str, version: str) -> str:
    return f"config:{key}:{version}"


def serialize_config(config: ConfigModel, value: dict[str, Any] | None = None) -> ConfigView:
    return {
        "key": config.key,
        "value": config.value if value is None else value,
        "isPublic": config.is_public,
        "createdAt": iso_timestamp(config.created_at),
        "updatedAt": iso_timestamp(config.updated_at),
    }


def _config_not_found() -> Exception:
    return not_found({"error": "configNotFound"})


class AppConfigService:
    def __init__(self, session: AsyncSession, cache: Cache) -> None:
        self.repository = ConfigRepository(session)
        self.cache = cache

    async def find_public_by_key(self, key: str) -> ConfigView:
        config = await self._find_effective(key)
        if not config or not config["isPublic"]:
            raise _config_not_found()
        return config

    async def find_by_key(self, key: str) -> ConfigView:
        config = await self._find_effective(key)
        if not config:
            raise _config_not_found()
        return config

    async def get_value(self, key: str) -> Any:
        """登记项的配置值（`value.value`），未配置时返回登记的默认值（无默认值为 None）。"""
        config = await self._load(key, cache_missing=True)
        if config and isinstance(config["value"], dict) and "value" in config["value"]:
            return config["value"]["value"]
        definition = find_config_definition(key)
        return definition.default_value if definition and definition.has_default else None

    async def upsert_generic(
        self, key: str, value: dict[str, Any], is_public: bool | None = None
    ) -> ConfigView:
        """通用写接口入口，拒绝由专用接口管理的登记项。"""
        definition = find_config_definition(key)
        if definition and definition.dedicated:
            raise unprocessable({"errors": {"key": "configManagedByDedicatedEndpoint"}})
        return await self.upsert(key, value, is_public)

    async def upsert(
        self, key: str, value: dict[str, Any], is_public: bool | None = None
    ) -> ConfigView:
        """登记项的 isPublic 以登记表为准。"""
        logger.info("Upsert config: %s", key)
        definition = find_config_definition(key)
        stored = await self.repository.upsert(
            key, value, definition.is_public if definition else is_public
        )

        async def bump_version() -> None:
            await self.cache.set(config_version_key(key), str(uuid.uuid4()), CONFIG_VERSION_TTL_MS)

        after_commit(self.repository.session, bump_version)
        config = serialize_config(stored, value)
        if definition:
            config["isPublic"] = definition.is_public
        return config

    async def _find_effective(self, key: str) -> ConfigView | None:
        definition = find_config_definition(key)
        stored = await self._load(key, cache_missing=definition is not None)
        if stored:
            return {**stored, "isPublic": definition.is_public} if definition else stored
        if definition is None or not definition.has_default:
            return None
        return {
            "key": key,
            "value": {"value": definition.default_value},
            "isPublic": definition.is_public,
            "createdAt": None,
            "updatedAt": None,
        }

    async def _load(self, key: str, *, cache_missing: bool) -> ConfigView | None:
        version = await self.cache.get(config_version_key(key))
        cache_key = config_cache_key(key, version if version is not None else "0")
        cached = await self.cache.get(cache_key)
        if cached:
            config: ConfigView | None = cached.get("config")
            return dict(config) if config else None
        stored = await self.repository.find_by_key(key)
        config = serialize_config(stored) if stored else None
        if config or cache_missing:
            await self.cache.set(cache_key, {"config": config}, CONFIG_CACHE_TTL_MS)
        return config
