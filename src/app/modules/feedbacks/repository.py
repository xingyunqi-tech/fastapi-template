from typing import Any
lazy from collections.abc import Sequence

from sqlalchemy import func, null, select, update
lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import lazy_total, offset_limit
from app.modules.feedbacks.models import FeedbackModel


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, values: dict[str, Any]) -> FeedbackModel:
        feedback = FeedbackModel(**{k: v for k, v in values.items() if v is not None})
        self.session.add(feedback)
        await self.session.flush()
        await self.session.refresh(feedback)
        return feedback

    async def find_by_id(self, feedback_id: int) -> FeedbackModel | None:
        return await self.session.scalar(
            select(FeedbackModel).where(FeedbackModel.id == feedback_id)
        )

    async def find_page(
        self,
        *,
        user_id: int,
        types: Sequence[str],
        status: str | None,
        page: float,
        limit: float,
    ) -> tuple[list[FeedbackModel], float]:
        conditions = [FeedbackModel.type.in_(types), FeedbackModel.user_id == user_id]
        if status:
            conditions.append(FeedbackModel.status == status)
        offset, size = offset_limit(page, limit)
        rows = list(
            await self.session.scalars(
                select(FeedbackModel)
                .where(*conditions)
                .order_by(FeedbackModel.created_at.desc(), FeedbackModel.id.desc())
                .offset(offset)
                .limit(size)
            )
        )
        total = lazy_total(len(rows), page, limit)
        if total is None:
            count = await self.session.scalar(
                select(func.count()).select_from(FeedbackModel).where(*conditions)
            )
            total = int(count or 0)
        return rows, total

    async def anonymize_by_user(self, user_id: int) -> None:
        await self.session.execute(
            update(FeedbackModel)
            .where(FeedbackModel.user_id == user_id)
            .values(user_id=None, contact=None, images=null(), updated_at=func.now())
        )
