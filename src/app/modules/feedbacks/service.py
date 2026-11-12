"""用户反馈：提交、列表（默认只含通用类型）与详情（仅本人且为可查询类型）。"""

from enum import StrEnum
from typing import Any, Final
lazy from collections.abc import Mapping

from app.common.exceptions import HttpError, not_found_error
lazy from app.modules.feedbacks.models import FeedbackModel
lazy from app.modules.feedbacks.repository import FeedbackRepository


class FeedbackType(StrEnum):
    BUG = "bug"
    SUGGESTION = "suggestion"
    COMPLAINT = "complaint"
    OTHER = "other"
    QUESTION_ERROR = "question_error"
    AI_CONTENT = "ai_content"


GENERAL_FEEDBACK_TYPES: Final = (
    FeedbackType.BUG,
    FeedbackType.SUGGESTION,
    FeedbackType.COMPLAINT,
    FeedbackType.OTHER,
)
USER_QUERYABLE_FEEDBACK_TYPES: Final = (*GENERAL_FEEDBACK_TYPES, FeedbackType.QUESTION_ERROR)
PENDING: Final = "pending"


def _feedback_not_found() -> HttpError:
    return not_found_error("feedbackNotFound")


class FeedbacksService:
    def __init__(self, repository: FeedbackRepository) -> None:
        self.repository = repository

    async def create(self, user_id: int, body: Mapping[str, Any]) -> FeedbackModel:
        return await self.repository.create(
            {
                "user_id": user_id,
                "type": body["type"],
                "content": body["content"],
                "images": body.get("images"),
                "contact": body.get("contact"),
                "status": PENDING,
            }
        )

    async def find_page(
        self, user_id: int, *, type_: str | None, status: str | None, page: float, limit: float
    ) -> tuple[list[FeedbackModel], float]:
        return await self.repository.find_page(
            user_id=user_id,
            types=[type_] if type_ else list(GENERAL_FEEDBACK_TYPES),
            status=status,
            page=page,
            limit=limit,
        )

    async def find_own(self, feedback_id: int, user_id: int) -> FeedbackModel:
        feedback = await self.repository.find_by_id(feedback_id)
        if (
            feedback is None
            or feedback.user_id != user_id
            or feedback.type not in USER_QUERYABLE_FEEDBACK_TYPES
        ):
            raise _feedback_not_found()
        return feedback
