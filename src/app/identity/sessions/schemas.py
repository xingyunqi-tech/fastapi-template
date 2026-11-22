from uuid import UUID

from app.common.types import ApiModel, UtcDatetime


class Session(ApiModel):
    id: UUID
    user_agent: str | None
    ip: str | None
    created_at: UtcDatetime
    last_active_at: UtcDatetime | None
    current: bool
