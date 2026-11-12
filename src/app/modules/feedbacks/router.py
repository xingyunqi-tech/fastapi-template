from typing import Annotated, Any, Final

lazy from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import pagination
from app.common.params import Body, IdParam, Query
from app.common.routing import ApiRouter, Auth
from app.contract.schemas import CreateFeedbackDto, Feedback, PaginationFeedbackResponseDto
from app.core.auth.deps import CurrentUser
from app.infrastructure.database.session import DbSession
from app.modules.feedbacks.repository import FeedbackRepository
from app.modules.feedbacks.schemas import QueryFeedbackDto
from app.modules.feedbacks.serializers import serialize_feedback
from app.modules.feedbacks.service import FeedbacksService

MAX_LIMIT: Final = 50

router = ApiRouter(prefix="/v1/feedbacks", tags=["Feedbacks"])


def build_feedbacks_service(session: AsyncSession) -> FeedbacksService:
    return FeedbacksService(FeedbackRepository(session))


@router.endpoint(
    "POST",
    "",
    operation_id="createFeedback",
    auth=Auth.JWT,
    response_model=Feedback,
    summary="提交反馈",
)
async def create_feedback(
    body: Annotated[CreateFeedbackDto, Body(CreateFeedbackDto)],
    user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    feedback = await build_feedbacks_service(session).create(
        user.id, body.model_dump(by_alias=True, exclude_unset=True)
    )
    return serialize_feedback(feedback)


@router.endpoint(
    "GET",
    "",
    operation_id="getFeedbacks",
    auth=Auth.JWT,
    response_model=PaginationFeedbackResponseDto,
    summary="获取反馈列表",
)
async def get_feedbacks(
    query: Annotated[QueryFeedbackDto, Query(QueryFeedbackDto)],
    user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    page = query.page if query.page is not None else 1
    limit = query.limit if query.limit is not None else 10
    if limit > MAX_LIMIT:
        limit = MAX_LIMIT
    data, total = await build_feedbacks_service(session).find_page(
        user.id, type_=query.type_, status=query.status, page=page, limit=limit
    )
    return pagination([serialize_feedback(f) for f in data], total, page, limit)


@router.endpoint(
    "GET",
    "/{id}",
    operation_id="getFeedback",
    auth=Auth.JWT,
    response_model=Feedback,
    summary="获取反馈详情",
)
async def get_feedback(id: IdParam, user: CurrentUser, session: DbSession) -> dict[str, Any]:
    return serialize_feedback(await build_feedbacks_service(session).find_own(id, user.id))
