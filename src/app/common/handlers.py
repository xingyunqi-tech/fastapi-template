"""注册全局异常处理：HttpError、校验失败、路由未匹配（含方法不符）统一输出 nestjs 信封。"""

import logging
from typing import TYPE_CHECKING

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
lazy from starlette.responses import Response

from app.common.exceptions import HttpError, bad_request, not_found
from app.common.middleware import current_request_id
from app.common.responses import JsonResponse
from app.common.validation.engine import ValidationFailedError
lazy from app.common.errors import ErrorTranslator

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

logger = logging.getLogger("app.http")


def original_url(request: Request) -> str:
    raw_path = request.scope.get("raw_path")
    path = raw_path.decode("latin-1") if raw_path else request.url.path
    query = request.scope.get("query_string", b"").decode("latin-1")
    return f"{path}?{query}" if query else path


def _respond(translator: ErrorTranslator, request: Request, exc: BaseException) -> Response:
    body = translator.build(exc)
    extra = {"path": request.url.path, "request_id": current_request_id(), "status": body.status}
    if body.status >= 500:
        logger.error("%s: %s", body.log_error, body.message, extra=extra)
    elif body.status >= 400:
        logger.warning("%s: %s", body.log_error, body.message, extra=extra)
    return JsonResponse(body.payload(), status_code=body.status, headers=body.headers)


def register_exception_handlers(app: FastAPI, translator: ErrorTranslator) -> None:
    async def http_error(request: Request, exc: Exception) -> Response:
        return _respond(translator, request, exc)

    async def validation_failed(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, ValidationFailedError):
            raise TypeError(exc)
        error = HttpError(exc.status, {"status": exc.status, "errors": exc.errors})
        return _respond(translator, request, error)

    async def route_not_matched(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, StarletteHTTPException):
            raise TypeError(exc)
        if exc.status_code in {404, 405}:
            error = not_found(f"Cannot {request.method} {original_url(request)}")
        else:
            error = HttpError.build(exc.status_code)
        return _respond(translator, request, error)

    async def request_validation(request: Request, exc: Exception) -> Response:
        return _respond(translator, request, bad_request())

    app.add_exception_handler(HttpError, http_error)
    app.add_exception_handler(ValidationFailedError, validation_failed)
    app.add_exception_handler(StarletteHTTPException, route_not_matched)
    app.add_exception_handler(RequestValidationError, request_validation)
