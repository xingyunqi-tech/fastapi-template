"""nestjs AllExceptionsFilter 的移植：把异常转换为 `{code, message, data}`。"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.common.exceptions import (
    NEST_HTTP_STATUS,
    STATUS_TO_I18N_KEY,
    BusinessException,
    HttpError,
    TooManyRequests,
    find_unique_violation,
    unique_violation_response,
)
lazy from app.common.i18n import I18n

DEFAULT_EXCEPTION_MESSAGES: Final = {
    "TOO_MANY_REQUESTS": "Too many requests, please try again later",
    "INTERNAL_SERVER_ERROR": "Internal server error",
}

_DEFAULT_EXCEPTION_SUFFIXES: Final = (
    "Bad Request Exception",
    "Unauthorized Exception",
    "Forbidden Exception",
    "Not Found Exception",
    "Unprocessable Entity Exception",
    "Internal Server Error Exception",
)
_DEFAULT_DESCRIPTION_VALUES: Final = frozenset(
    {
        "Bad Request",
        "Unauthorized",
        "Forbidden",
        "Not Found",
        "Conflict",
        "Gone",
        "Unprocessable Entity",
        "Too Many Requests",
        "Internal Server Error",
    }
)


@dataclass(frozen=True, slots=True)
class ErrorBody:
    status: int
    message: str | list[str]
    data: Any
    headers: Mapping[str, str]
    log_error: str

    def payload(self) -> dict[str, Any]:
        return {"code": self.status, "message": self.message, "data": self.data}


class ErrorTranslator:
    def __init__(self, i18n: I18n) -> None:
        self.i18n = i18n

    def _translate(self, key: str) -> str | None:
        return self.i18n.translate(f"common.{key}")

    def _default_message(self, status: int) -> str:
        key = STATUS_TO_I18N_KEY.get(status)
        if key:
            translated = self._translate(key)
            if translated:
                return translated
        return NEST_HTTP_STATUS.get(status, "Error")

    @staticmethod
    def _is_default_message(message: Any) -> bool:
        if not isinstance(message, str):
            return False
        return message in _DEFAULT_DESCRIPTION_VALUES or any(
            suffix in message for suffix in _DEFAULT_EXCEPTION_SUFFIXES
        )

    def _translated_message(self, raw: Any, status: int, error_code: str | None) -> str | list[str]:
        if not raw or self._is_default_message(raw):
            if error_code:
                return self._translate(error_code) or error_code
            return self._default_message(status)
        if isinstance(raw, str):
            return self._translate(raw) or raw
        if isinstance(raw, list):
            return [self._translate(m) or m if isinstance(m, str) else m for m in raw]
        return raw  # type: ignore[no-any-return]

    def _translate_errors(self, errors: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: (self._translate(value) or value) if isinstance(value, str) else value
            for key, value in errors.items()
        }

    def build(self, exc: BaseException) -> ErrorBody:
        if isinstance(exc, TooManyRequests):
            return ErrorBody(
                429,
                self._translate("tooManyRequests")
                or DEFAULT_EXCEPTION_MESSAGES["TOO_MANY_REQUESTS"],
                None,
                exc.headers,
                "Too Many Requests",
            )
        violation = find_unique_violation(exc)
        if violation is not None:
            mapped = unique_violation_response(violation.constraint)
            message = (
                self._translate(mapped.error) or mapped.error
                if mapped.error
                else self._default_message(mapped.status)
            )
            data = self._translate_errors(mapped.errors) if mapped.errors else None
            return ErrorBody(
                mapped.status,
                message,
                data,
                {},
                f"Unique constraint violation {violation.constraint or 'unknown'}",
            )
        if isinstance(exc, HttpError):
            return self._http_error(exc)
        return ErrorBody(
            500,
            self._translate("internalServerError")
            or DEFAULT_EXCEPTION_MESSAGES["INTERNAL_SERVER_ERROR"],
            None,
            {},
            "Internal Server Error",
        )

    def _http_error(self, exc: HttpError) -> ErrorBody:
        status = exc.status
        response = exc.response
        errors: Any = None
        if isinstance(response, str):
            message: str | list[str] = self._translate(response) or response
            error = type(exc).__name__
        elif isinstance(response, Mapping):
            errors = response.get("errors")
            raw_message = response.get("message")
            raw_error = response.get("error") if isinstance(response.get("error"), str) else None
            error_code = (
                raw_error if raw_error and raw_error not in _DEFAULT_DESCRIPTION_VALUES else None
            )
            message = self._translated_message(raw_message, status, error_code)
            error = raw_error or type(exc).__name__
            if isinstance(errors, Mapping) and not isinstance(exc, BusinessException):
                errors = self._translate_errors(errors)
        else:
            message = self._default_message(status)
            error = type(exc).__name__
        return ErrorBody(status, message, errors, exc.headers, error)
