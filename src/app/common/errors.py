"""约定 §2.4 错误码登记表与 AppError。"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    status: int
    code: int


ERRORS: Final[Mapping[str, ErrorSpec]] = {
    "common.badRequest": ErrorSpec(400, 40000),
    "common.malformedJson": ErrorSpec(400, 40001),
    "common.unauthorized": ErrorSpec(401, 40100),
    "auth.tokenExpired": ErrorSpec(401, 40101),
    "auth.invalidCredentials": ErrorSpec(401, 40102),
    "auth.refreshTokenReused": ErrorSpec(401, 40103),
    "auth.refreshTokenInvalid": ErrorSpec(401, 40104),
    "common.forbidden": ErrorSpec(403, 40300),
    "auth.accountDisabled": ErrorSpec(403, 40301),
    "auth.emailNotVerified": ErrorSpec(403, 40302),
    "auth.reauthRequired": ErrorSpec(403, 40303),
    "common.notFound": ErrorSpec(404, 40400),
    "auth.providerDisabled": ErrorSpec(404, 40401),
    "auth.sessionNotFound": ErrorSpec(404, 40402),
    "user.notFound": ErrorSpec(404, 40403),
    "common.methodNotAllowed": ErrorSpec(405, 40500),
    "common.conflict": ErrorSpec(409, 40900),
    "user.emailTaken": ErrorSpec(409, 40901),
    "user.phoneTaken": ErrorSpec(409, 40902),
    "common.idempotencyInProgress": ErrorSpec(409, 40903),
    "role.lastAdmin": ErrorSpec(409, 40904),
    "common.payloadTooLarge": ErrorSpec(413, 41300),
    "file.tooLarge": ErrorSpec(413, 41301),
    "common.unsupportedMediaType": ErrorSpec(415, 41500),
    "common.validationFailed": ErrorSpec(422, 42200),
    "auth.verificationCodeInvalid": ErrorSpec(422, 42201),
    "auth.verificationTokenInvalid": ErrorSpec(422, 42202),
    "auth.passwordIncorrect": ErrorSpec(422, 42203),
    "file.typeNotAllowed": ErrorSpec(422, 42204),
    "common.idempotencyKeyMismatch": ErrorSpec(422, 42205),
    "auth.providerCodeInvalid": ErrorSpec(422, 42206),
    "common.tooManyRequests": ErrorSpec(429, 42900),
    "auth.sendTooFrequent": ErrorSpec(429, 42901),
    "auth.verificationLocked": ErrorSpec(429, 42902),
    "common.internalError": ErrorSpec(500, 50000),
    "common.badGateway": ErrorSpec(502, 50200),
    "common.serviceUnavailable": ErrorSpec(503, 50300),
}

STATUS_ERROR_KEYS: Final[Mapping[int, str]] = {
    400: "common.badRequest",
    401: "common.unauthorized",
    403: "common.forbidden",
    404: "common.notFound",
    405: "common.methodNotAllowed",
    409: "common.conflict",
    413: "common.payloadTooLarge",
    415: "common.unsupportedMediaType",
    422: "common.validationFailed",
    429: "common.tooManyRequests",
    502: "common.badGateway",
    503: "common.serviceUnavailable",
}


def error_key_for_status(status: int) -> str:
    """HTTP 状态对应的通用 errorKey；未登记的 4xx 为 `badRequest`，其余为 `internalError`。"""
    if status in STATUS_ERROR_KEYS:
        return STATUS_ERROR_KEYS[status]
    return "common.badRequest" if 400 <= status < 500 else "common.internalError"


@dataclass(frozen=True, slots=True)
class FieldError:
    """约定 §2.3 `data.errors` 的一项；`message` 为空时按 `rule` 翻译。"""

    field: str
    rule: str
    params: Mapping[str, Any] | None = None
    message: str | None = None


DEPENDENCY_RETRY_AFTER_SECONDS: Final = 5


class DependencyUnavailableError(Exception):
    """外部依赖（Redis 等）不可用：响应 503 `common.serviceUnavailable`，请求事务回滚。"""


class AppError(Exception):
    """按 `error_key` 查登记表得到 HTTP 状态与 `code`；未登记的键在构造时抛 KeyError。"""

    def __init__(
        self,
        error_key: str,
        *,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        message: str | None = None,
    ) -> None:
        self.spec = ERRORS[error_key]
        self.error_key = error_key
        self.data = data
        self.headers = dict(headers or {})
        self.message = message
        super().__init__(message or error_key)

    @property
    def status(self) -> int:
        return self.spec.status

    @property
    def code(self) -> int:
        return self.spec.code


class ValidationFailed(AppError):
    """422 `common.validationFailed`，`data.errors` 由 `errors` 生成。"""

    def __init__(self, errors: Sequence[FieldError]) -> None:
        super().__init__("common.validationFailed")
        self.errors = list(errors)


def field_error(field: str, rule: str, **params: Any) -> ValidationFailed:
    return ValidationFailed([FieldError(field, rule, params or None)])
