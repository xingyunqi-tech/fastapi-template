"""与 nestjs Feedback 输出一致的序列化（键顺序同 FeedbackMapper，images 为空时不输出）。"""

from typing import Any

from app.common.responses import iso_timestamp
lazy from app.modules.feedbacks.models import FeedbackModel


def _uuid(value: object) -> str | None:
    return str(value) if value is not None else None


def serialize_feedback(feedback: FeedbackModel) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": feedback.id,
        "userId": feedback.user_id,
        "type": feedback.type,
        "content": feedback.content,
    }
    if feedback.images is not None:
        body["images"] = feedback.images
    body.update(
        {
            "contact": feedback.contact,
            "status": feedback.status,
            "reply": feedback.reply,
            "questionId": _uuid(feedback.question_id),
            "questionVersion": feedback.question_version,
            "reason": feedback.reason,
            "errataId": _uuid(feedback.errata_id),
            "messageId": _uuid(feedback.message_id),
            "messageSnapshot": feedback.message_snapshot,
            "createdAt": iso_timestamp(feedback.created_at),
            "updatedAt": iso_timestamp(feedback.updated_at),
        }
    )
    return body
