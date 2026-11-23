from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from app.config.base import SettingsGroup


class EventsTransport(StrEnum):
    NONE = "none"
    LOG = "log"
    REDIS_STREAM = "redis_stream"


class EventsSettings(SettingsGroup):
    """约定 §10 的 `user.deleted`；stream 名由 monorepo 分配，不加服务前缀。"""

    transport: EventsTransport = Field(
        default=EventsTransport.LOG, validation_alias="EVENTS_TRANSPORT"
    )
    redis_stream: str | None = Field(default=None, validation_alias="EVENTS_REDIS_STREAM")

    @model_validator(mode="after")
    def _stream_required(self) -> Self:
        if self.transport is EventsTransport.REDIS_STREAM and not self.redis_stream:
            raise ValueError("EVENTS_REDIS_STREAM: required when EVENTS_TRANSPORT=redis_stream")
        return self
