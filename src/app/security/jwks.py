"""验证方的 JWKS（约定 §5.1）：按 `Cache-Control` 缓存，未知 `kid` 触发刷新，间隔不少于 30 秒。"""

import asyncio
import logging
import re
import time
from typing import Any, Final
lazy from collections.abc import Callable

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.security.keys import b64decode_url

MIN_REFRESH_INTERVAL_S: Final = 30.0
DEFAULT_MAX_AGE_S: Final = 300
FETCH_TIMEOUT_S: Final = 5.0
_MAX_AGE: Final = re.compile(r"(?:^|,)\s*max-age\s*=\s*(\d+)\s*(?:,|$)", re.IGNORECASE)

logger = logging.getLogger("app.auth")


class JwksUnavailableError(Exception):
    """尚未取得任何公钥且 JWKS 不可用。"""


def max_age(cache_control: str | None) -> int:
    match = _MAX_AGE.search(cache_control or "")
    return int(match.group(1)) if match else DEFAULT_MAX_AGE_S


def parse_jwks(body: Any) -> dict[str, Ed25519PublicKey]:
    """只取 `kty: OKP`、`crv: Ed25519` 且带 `kid` 的 JWK，其余跳过。"""
    entries = body.get("keys") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        raise ValueError("JWKS must contain a keys array")
    keys: dict[str, Ed25519PublicKey] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kid, x = entry.get("kid"), entry.get("x")
        if entry.get("kty") != "OKP" or entry.get("crv") != "Ed25519":
            continue
        if entry.get("alg", "EdDSA") != "EdDSA" or entry.get("use", "sig") != "sig":
            continue
        if not isinstance(kid, str) or not isinstance(x, str):
            continue
        try:
            keys[kid] = Ed25519PublicKey.from_public_bytes(b64decode_url(x))
        except ValueError:
            continue
    return keys


class JwksKeySet:
    """并发请求共用同一次刷新；失败后 30 秒内不再请求，期间沿用缓存，无缓存时抛不可用。"""

    def __init__(
        self,
        url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self.transport = transport
        self.clock = clock
        self.keys: dict[str, Ed25519PublicKey] = {}
        self.loaded = False
        self.expires_at = 0.0
        self.refreshed_at = -MIN_REFRESH_INTERVAL_S
        self._lock = asyncio.Lock()

    async def get(self, kid: str) -> Ed25519PublicKey | None:
        if self.clock() >= self.expires_at:
            await self.refresh()
        if not self.loaded:
            raise JwksUnavailableError
        key = self.keys.get(kid)
        if key is None and self.clock() - self.refreshed_at >= MIN_REFRESH_INTERVAL_S:
            await self.refresh()
            key = self.keys.get(kid)
        return key

    async def refresh(self) -> None:
        requested_at = self.clock()
        async with self._lock:
            if self.refreshed_at >= requested_at:
                return
            try:
                keys, ttl = await self._fetch()
            except Exception as error:
                self.refreshed_at = self.clock()
                self.expires_at = self.refreshed_at + MIN_REFRESH_INTERVAL_S
                logger.warning("Failed to fetch JWKS from %s: %s", self.url, error)
                return
            self.refreshed_at = self.clock()
            self.keys = keys
            self.loaded = True
            self.expires_at = self.refreshed_at + max(ttl, MIN_REFRESH_INTERVAL_S)

    async def _fetch(self) -> tuple[dict[str, Ed25519PublicKey], int]:
        async with httpx.AsyncClient(transport=self.transport, timeout=FETCH_TIMEOUT_S) as client:
            response = await client.get(self.url)
        response.raise_for_status()
        return parse_jwks(response.json()), max_age(response.headers.get("cache-control"))
