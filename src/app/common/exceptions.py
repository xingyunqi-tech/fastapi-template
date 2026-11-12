"""与 NestJS HttpException 体系等价的异常，以及业务错误键表、唯一约束映射。"""

from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Final
lazy from collections.abc import Mapping

DEFAULT_DESCRIPTIONS: Final[dict[int, str]] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    410: "Gone",
    413: "Payload Too Large",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
}

NEST_HTTP_STATUS: Final[dict[int, str]] = {
    100: "CONTINUE",
    101: "SWITCHING_PROTOCOLS",
    102: "PROCESSING",
    103: "EARLYHINTS",
    200: "OK",
    201: "CREATED",
    202: "ACCEPTED",
    203: "NON_AUTHORITATIVE_INFORMATION",
    204: "NO_CONTENT",
    205: "RESET_CONTENT",
    206: "PARTIAL_CONTENT",
    207: "MULTI_STATUS",
    208: "ALREADY_REPORTED",
    210: "CONTENT_DIFFERENT",
    300: "AMBIGUOUS",
    301: "MOVED_PERMANENTLY",
    302: "FOUND",
    303: "SEE_OTHER",
    304: "NOT_MODIFIED",
    307: "TEMPORARY_REDIRECT",
    308: "PERMANENT_REDIRECT",
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    402: "PAYMENT_REQUIRED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    406: "NOT_ACCEPTABLE",
    407: "PROXY_AUTHENTICATION_REQUIRED",
    408: "REQUEST_TIMEOUT",
    409: "CONFLICT",
    410: "GONE",
    411: "LENGTH_REQUIRED",
    412: "PRECONDITION_FAILED",
    413: "PAYLOAD_TOO_LARGE",
    414: "URI_TOO_LONG",
    415: "UNSUPPORTED_MEDIA_TYPE",
    416: "REQUESTED_RANGE_NOT_SATISFIABLE",
    417: "EXPECTATION_FAILED",
    418: "I_AM_A_TEAPOT",
    421: "MISDIRECTED",
    422: "UNPROCESSABLE_ENTITY",
    423: "LOCKED",
    424: "FAILED_DEPENDENCY",
    428: "PRECONDITION_REQUIRED",
    429: "TOO_MANY_REQUESTS",
    456: "UNRECOVERABLE_ERROR",
    500: "INTERNAL_SERVER_ERROR",
    501: "NOT_IMPLEMENTED",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
    505: "HTTP_VERSION_NOT_SUPPORTED",
    507: "INSUFFICIENT_STORAGE",
    508: "LOOP_DETECTED",
}

STATUS_TO_I18N_KEY: Final[dict[int, str]] = {
    400: "badRequest",
    401: "unauthorized",
    403: "forbidden",
    404: "notFound",
    409: "conflict",
    410: "gone",
    422: "unprocessableEntity",
    429: "tooManyRequests",
    500: "internalServerError",
}

BUSINESS_ERRORS: Final[dict[str, int]] = {
    "sessionTakenOver": 409,
    "sessionClosed": 409,
    "questionInMockInProgress": 409,
    "answerHidden": 403,
    "selfAssessmentNotAllowed": 409,
    "questionNotAccessible": 403,
    "noQuestionsAvailable": 422,
    "generateLimitExceeded": 429,
    "mockSessionBusy": 409,
    "paperRequired": 422,
    "reportNotAvailable": 409,
    "dailySessionCannotFinish": 409,
    "dailySessionCannotAbandon": 409,
    "aiUnavailable": 503,
    "aiQuotaExceeded": 429,
    "aiRateLimited": 429,
    "aiConsentRequired": 403,
    "phoneBindingRequired": 403,
    "contentBlocked": 422,
    "aiConversationOutdated": 409,
    "errataInProgress": 409,
    "answerChangeNotAllowed": 422,
}
BUSINESS_ERROR_KEY_FIELD: Final = "errorKey"


class HttpError(Exception):
    """对应 Nest 的 HttpException：`response` 为字符串或对象（message / error / errors）。"""

    def __init__(
        self,
        status: int,
        response: str | Mapping[str, Any] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(response)
        self.status = status
        self.response = response
        self.headers = dict(headers or {})

    @classmethod
    def build(
        cls, status: int, object_or_message: str | Mapping[str, Any] | None = None
    ) -> HttpError:
        """Nest 内置异常（NotFoundException 等）的 createBody 规则。"""
        description = DEFAULT_DESCRIPTIONS.get(status) or HTTPStatus(status).phrase
        if object_or_message is None:
            return cls(status, {"statusCode": status, "message": description})
        if isinstance(object_or_message, str):
            return cls(
                status,
                {"statusCode": status, "message": object_or_message, "error": description},
            )
        return cls(status, object_or_message)


def bad_request(body: str | Mapping[str, Any] | None = None) -> HttpError:
    return HttpError.build(400, body)


def unauthorized(body: str | Mapping[str, Any] | None = None) -> HttpError:
    return HttpError.build(401, body)


def forbidden(body: str | Mapping[str, Any] | None = None) -> HttpError:
    return HttpError.build(403, body)


def not_found(body: str | Mapping[str, Any] | None = None) -> HttpError:
    return HttpError.build(404, body)


def conflict(body: str | Mapping[str, Any] | None = None) -> HttpError:
    return HttpError.build(409, body)


def unprocessable(body: str | Mapping[str, Any] | None = None) -> HttpError:
    return HttpError.build(422, body)


def validation_error(field: str, error: str) -> HttpError:
    """nestjs `throwValidationError(field, error)`。"""
    return unprocessable({"status": 422, "errors": {field: error}})


def validation_errors(errors: Mapping[str, Any]) -> HttpError:
    return unprocessable({"status": 422, "errors": dict(errors)})


def not_found_error(error: str) -> HttpError:
    """nestjs `throwNotFoundError(error)`：message 为 `common.<error>` 译文。"""
    return not_found({"status": 404, "error": error})


class BusinessException(HttpError):
    def __init__(self, error_key: str, **details: Any) -> None:
        if error_key not in BUSINESS_ERRORS:
            raise KeyError(f"未登记的业务错误键：{error_key}")
        super().__init__(
            BUSINESS_ERRORS[error_key],
            {"error": error_key, "errors": {**details, BUSINESS_ERROR_KEY_FIELD: error_key}},
        )
        self.error_key = error_key


class TooManyRequests(HttpError):
    """对应 @nestjs/throttler 的 ThrottlerException。"""

    def __init__(self, headers: Mapping[str, str] | None = None) -> None:
        super().__init__(429, "ThrottlerException: Too Many Requests", headers=headers)


@dataclass(frozen=True, slots=True)
class UniqueViolationResponse:
    status: int
    errors: dict[str, str] | None = None
    error: str | None = None


UNIQUE_CONSTRAINT_RESPONSES: Final[dict[str, UniqueViolationResponse]] = {
    "UQ_e12875dfb3b1d92d7d7c5377e22": UniqueViolationResponse(
        422, errors={"email": "emailAlreadyExists"}
    ),
    "user_phone_key": UniqueViolationResponse(422, errors={"phone": "phoneAlreadyExists"}),
    "user_wechatOpenId_key": UniqueViolationResponse(
        422, errors={"wechatOpenId": "wechatOpenIdAlreadyExists"}
    ),
    "user_alipayOpenId_key": UniqueViolationResponse(
        422, errors={"alipayOpenId": "alipayOpenIdAlreadyExists"}
    ),
    "REL_75e2be4ce11d447ef43be0e374": UniqueViolationResponse(
        422, errors={"photo": "alreadyExists"}
    ),
    "UQ_favorite_user_target": UniqueViolationResponse(409, error="alreadyFavorited"),
    "UQ_question_subjectId_contentHash": UniqueViolationResponse(409, error="duplicateQuestion"),
    "UQ_order_orderNo": UniqueViolationResponse(500),
    "UQ_share_shareCode": UniqueViolationResponse(500),
}

PG_UNIQUE_VIOLATION: Final = "23505"


def unique_violation_response(constraint: str | None) -> UniqueViolationResponse:
    return UNIQUE_CONSTRAINT_RESPONSES.get(constraint or "") or UniqueViolationResponse(409)


@dataclass(frozen=True, slots=True)
class UniqueViolation:
    constraint: str | None


def find_unique_violation(exc: BaseException) -> UniqueViolation | None:
    """被抛出的异常本身（或其 SQLAlchemy `orig`）为 PostgreSQL 23505 时返回约束名。"""
    current: BaseException | None = exc
    while current is not None:
        code = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if code == PG_UNIQUE_VIOLATION:
            constraint = getattr(current, "constraint_name", None)
            diag = getattr(current, "diag", None)
            if constraint is None and diag is not None:
                constraint = getattr(diag, "constraint_name", None)
            return UniqueViolation(constraint)
        orig = getattr(current, "orig", None)
        current = orig if isinstance(orig, BaseException) and orig is not current else None
    return None
