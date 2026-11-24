"""约定 §10 的事件消费：Redis Stream 消费组（组名 `OTEL_SERVICE_NAME`），处理成功后 ACK。

失败的消息留在 pending，空闲超过 `min_idle_ms` 后重领；投递满 `max_deliveries` 次记 error 后 ACK。
"""

import asyncio
import logging
import os
import socket
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final
lazy from uuid import UUID

from cloudevents.core.v1.event import CloudEvent
from redis.exceptions import ResponseError
lazy from redis.asyncio import Redis

from app.infrastructure.account_data import registered_cleaners, run_cleaners
from app.infrastructure.events import STREAM_FIELD, USER_DELETED, decode
lazy from app.common.context import AppContext

type EventHandler = Callable[[CloudEvent], Awaitable[None]]

MAX_DELIVERIES: Final = 5
MIN_IDLE_MS: Final = 60_000
BLOCK_MS: Final = 5_000
BATCH_SIZE: Final = 10
ERROR_BACKOFF_S: Final = 1.0

logger = logging.getLogger("app.events")


class EventConsumer:
    def __init__(
        self,
        redis: Redis,
        stream: str,
        group: str,
        handlers: Mapping[str, EventHandler],
        *,
        consumer: str | None = None,
        max_deliveries: int = MAX_DELIVERIES,
        min_idle_ms: int = MIN_IDLE_MS,
        block_ms: int = BLOCK_MS,
    ) -> None:
        self.redis = redis
        self.stream = stream
        self.group = group
        self.handlers = handlers
        self.consumer = consumer or f"{socket.gethostname()}-{os.getpid()}"
        self.max_deliveries = max_deliveries
        self.min_idle_ms = min_idle_ms
        self.block_ms = block_ms

    async def ensure_group(self) -> None:
        """消费组不存在时从 stream 开头创建。"""
        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    async def poll(self, block_ms: int | None = BLOCK_MS) -> int:
        """先重领超时未 ACK 的消息，再读取新消息；返回本次处理的消息数。"""
        handled = await self._reclaim()
        response: Any = await self.redis.xreadgroup(
            self.group, self.consumer, {self.stream: ">"}, count=BATCH_SIZE, block=block_ms
        )
        for _, entries in response or []:
            for message_id, fields in entries:
                await self._handle(message_id, fields)
                handled += 1
        return handled

    async def run(self) -> None:
        """出错时记日志并重试；消费组缺失（启动时或运行中被删除）时重新创建。"""
        ready = False
        while True:
            try:
                if not ready:
                    await self.ensure_group()
                    ready = True
                await self.poll(self.block_ms)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if "NOGROUP" in str(error):
                    ready = False
                logger.exception("Event consumer of %s failed", self.stream)
                await asyncio.sleep(ERROR_BACKOFF_S)

    async def _reclaim(self) -> int:
        pending: Any = await self.redis.xpending_range(
            self.stream, self.group, min="-", max="+", count=BATCH_SIZE, idle=self.min_idle_ms
        )
        handled = 0
        for entry in pending:
            message_id = entry["message_id"]
            if entry["times_delivered"] >= self.max_deliveries:
                logger.error(
                    "Event %s on %s dropped after %d deliveries",
                    _text(message_id),
                    self.stream,
                    entry["times_delivered"],
                )
                await self.redis.xack(self.stream, self.group, message_id)
                continue
            claimed: Any = await self.redis.xclaim(
                self.stream, self.group, self.consumer, self.min_idle_ms, [message_id]
            )
            for claimed_id, fields in claimed:
                await self._handle(claimed_id, fields)
                handled += 1
        return handled

    async def _handle(self, message_id: Any, fields: Mapping[Any, Any] | None) -> None:
        raw = (fields or {}).get(STREAM_FIELD.encode())
        try:
            if not isinstance(raw, bytes):
                raise ValueError(f"missing {STREAM_FIELD} field")
            event = decode(raw)
        except Exception as error:
            logger.error("Malformed event %s on %s: %s", _text(message_id), self.stream, error)
            await self.redis.xack(self.stream, self.group, message_id)
            return
        handler = self.handlers.get(event.get_type())
        if handler is not None:
            try:
                await handler(event)
            except Exception:
                logger.exception("Failed to handle %s %s", event.get_type(), event.get_id())
                return
        await self.redis.xack(self.stream, self.group, message_id)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def user_deleted_handler(context: AppContext) -> EventHandler:
    """执行本服务登记的清理器；`data.userId` 无效时记 error 并跳过。"""

    async def handle(event: CloudEvent) -> None:
        data = event.get_data()
        user_id = data.get("userId") if isinstance(data, dict) else None
        try:
            parsed = UUID(user_id) if isinstance(user_id, str) else None
        except ValueError:
            parsed = None
        if parsed is None:
            logger.error("Event %s has no valid userId", event.get_id())
            return
        await run_cleaners(registered_cleaners(context), parsed)

    return handle


def build_consumer(context: AppContext) -> EventConsumer | None:
    """配置了数据库且 `EVENTS_TRANSPORT=redis_stream` 的验证方消费 `user.deleted`，其余为 None。"""
    settings = context.settings
    stream = settings.events.redis_stream
    if (
        settings.auth.is_issuer
        or context.database is None
        or context.redis is None
        or stream is None
        or settings.events.transport != "redis_stream"
    ):
        return None
    return EventConsumer(
        context.redis,
        stream,
        settings.telemetry.service_name,
        {USER_DELETED: user_deleted_handler(context)},
    )
