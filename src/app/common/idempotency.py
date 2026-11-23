"""约定 §6 `Idempotency-Key`：作用域 `(用户或 IP, 方法, 路径, 键)`，结果保存 24 小时。"""

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Final, Protocol

from fastapi import Header, Request
from starlette.responses import Response

from app.common.client import client_ip
from app.common.errors import AppError
from app.common.middleware import current_request_id
from app.infrastructure.redis import key as redis_key

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.common.context import AppContext

IDEMPOTENCY_HEADER: Final = "Idempotency-Key"
TTL_MS: Final = 24 * 60 * 60 * 1000
PENDING_TTL_MS: Final = 5 * 60 * 1000
STATE_KEY: Final = "idempotency_claim"
MEMORY_STORE_KEY: Final = "idempotency_memory_store"


class IdempotencyStore(Protocol):
    async def claim(self, key: str, value: dict[str, Any], ttl_ms: int) -> bool: ...

    async def get(self, key: str) -> dict[str, Any] | None: ...

    async def put(self, key: str, value: dict[str, Any], ttl_ms: int) -> None: ...

    async def delete(self, key: str) -> None: ...


class RedisIdempotencyStore:
    def __init__(self, client: Redis) -> None:
        self.client = client

    async def claim(self, key: str, value: dict[str, Any], ttl_ms: int) -> bool:
        return bool(await self.client.set(key, json.dumps(value), nx=True, px=ttl_ms))

    async def get(self, key: str) -> dict[str, Any] | None:
        raw = await self.client.get(key)
        return json.loads(raw) if raw is not None else None

    async def put(self, key: str, value: dict[str, Any], ttl_ms: int) -> None:
        await self.client.set(key, json.dumps(value), px=ttl_ms)

    async def delete(self, key: str) -> None:
        await self.client.delete(key)


class MemoryIdempotencyStore:
    def __init__(self) -> None:
        self.items: dict[str, tuple[float, dict[str, Any]]] = {}

    def _live(self, key: str) -> dict[str, Any] | None:
        item = self.items.get(key)
        if item is None:
            return None
        if item[0] <= time.monotonic():
            del self.items[key]
            return None
        return item[1]

    async def claim(self, key: str, value: dict[str, Any], ttl_ms: int) -> bool:
        if self._live(key) is not None:
            return False
        await self.put(key, value, ttl_ms)
        return True

    async def get(self, key: str) -> dict[str, Any] | None:
        return self._live(key)

    async def put(self, key: str, value: dict[str, Any], ttl_ms: int) -> None:
        self.items[key] = (time.monotonic() + ttl_ms / 1000, value)

    async def delete(self, key: str) -> None:
        self.items.pop(key, None)


def store_for(request: Request) -> IdempotencyStore:
    context: AppContext = request.app.state.context
    if context.redis is not None:
        return RedisIdempotencyStore(context.redis)
    store = getattr(request.app.state, MEMORY_STORE_KEY, None)
    if store is None:
        store = MemoryIdempotencyStore()
        setattr(request.app.state, MEMORY_STORE_KEY, store)
    return store


class IdempotentReplay(Exception):
    def __init__(self, response: Response) -> None:
        super().__init__("idempotent replay")
        self.response = response


@dataclass(frozen=True, slots=True)
class Claim:
    store: IdempotencyStore
    key: str
    body_hash: str

    async def finish(self, response: Response) -> None:
        """2xx / 4xx 保存状态码与响应体，5xx 释放该键。"""
        body = getattr(response, "body", None)
        if response.status_code >= 500 or not isinstance(body, bytes | memoryview):
            await self.store.delete(self.key)
            return
        await self.store.put(
            self.key,
            {
                "bodyHash": self.body_hash,
                "status": response.status_code,
                "contentType": response.headers.get("content-type"),
                "body": base64.b64encode(bytes(body)).decode(),
            },
            TTL_MS,
        )

    async def release(self) -> None:
        await self.store.delete(self.key)


def _subject(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None:
        return f"u:{user_id}"
    context: AppContext = request.app.state.context
    return f"ip:{client_ip(request, context.settings.app.trusted_proxies) or ''}"


async def idempotency_guard(
    request: Request,
    key_header: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
) -> None:
    """路由依赖：重复请求以首次结果回放；处理中 409，请求体不同 422。"""
    if not key_header:
        return
    context: AppContext = request.app.state.context
    scope = "\n".join([_subject(request), request.method, request.url.path, key_header])
    digest = hashlib.sha256(scope.encode()).hexdigest()
    key = redis_key(context.settings.telemetry.service_name, "idem", digest)
    body_hash = hashlib.sha256(await request.body()).hexdigest()
    store = store_for(request)
    if await store.claim(key, {"bodyHash": body_hash, "pending": True}, PENDING_TTL_MS):
        setattr(request.state, STATE_KEY, Claim(store, key, body_hash))
        return
    existing = await store.get(key) or {}
    if existing.get("bodyHash") != body_hash:
        raise AppError("common.idempotencyKeyMismatch")
    if existing.get("pending"):
        raise AppError("common.idempotencyInProgress")
    headers = {"content-type": existing["contentType"]} if existing.get("contentType") else None
    body = _with_current_request_id(base64.b64decode(existing["body"]))
    raise IdempotentReplay(Response(body, status_code=int(existing["status"]), headers=headers))


def _with_current_request_id(body: bytes) -> bytes:
    """错误响应体的 `requestId` 改为本次请求的值，其余内容与首次相同。"""
    try:
        payload = json.loads(body)
    except ValueError:
        return body
    if not isinstance(payload, dict) or "requestId" not in payload:
        return body
    payload["requestId"] = current_request_id() or ""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def claim_of(request: Request) -> Claim | None:
    claim = getattr(request.state, STATE_KEY, None)
    return claim if isinstance(claim, Claim) else None
