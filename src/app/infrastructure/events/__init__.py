"""约定 §10 的跨服务事件：CloudEvents 1.0 JSON，按 `EVENTS_TRANSPORT` 写日志或写入 Redis Stream。"""

import logging
import uuid
from typing import Final, Protocol

from cloudevents.core.formats.json import JSONFormat
from cloudevents.core.v1.event import CloudEvent
lazy from redis.asyncio import Redis

from app.config import EventsTransport
lazy from app.config import Settings

USER_DELETED: Final = "user.deleted"
STREAM_FIELD: Final = "event"

logger = logging.getLogger("app.events")
_format: Final = JSONFormat()


def user_deleted(service: str, user_id: str) -> CloudEvent:
    return CloudEvent(
        {
            "id": str(uuid.uuid7()),
            "type": USER_DELETED,
            "source": f"/{service}",
            "datacontenttype": "application/json",
        },
        {"userId": user_id},
    )


def encode(event: CloudEvent) -> bytes:
    return _format.write(event)


def decode(raw: str | bytes) -> CloudEvent:
    event = _format.read(None, raw)
    if not isinstance(event, CloudEvent):
        raise ValueError("unsupported CloudEvents version")
    return event


class EventPublisher(Protocol):
    async def publish(self, event: CloudEvent) -> None: ...


class NoopPublisher:
    async def publish(self, event: CloudEvent) -> None:
        return None


class LogPublisher:
    async def publish(self, event: CloudEvent) -> None:
        logger.info("Event published: %s", encode(event).decode())


class RedisStreamPublisher:
    def __init__(self, redis: Redis, stream: str) -> None:
        self.redis = redis
        self.stream = stream

    async def publish(self, event: CloudEvent) -> None:
        await self.redis.xadd(self.stream, {STREAM_FIELD: encode(event)})


def build_publisher(settings: Settings, redis: Redis | None) -> EventPublisher:
    transport = settings.events.transport
    if transport is EventsTransport.NONE:
        return NoopPublisher()
    if transport is EventsTransport.LOG:
        return LogPublisher()
    if redis is None or settings.events.redis_stream is None:
        raise RuntimeError(
            "EVENTS_TRANSPORT=redis_stream requires REDIS_URL and EVENTS_REDIS_STREAM"
        )
    return RedisStreamPublisher(redis, settings.events.redis_stream)
