"""全局异常处理：AppError、校验失败、路由未匹配与未捕获异常统一输出约定 §2.2 错误信封。"""

import logging
from typing import TYPE_CHECKING, Any

from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse
from starlette.routing import Match

from app.common.errors import ERRORS, AppError, FieldError, ValidationFailed, error_key_for_status
from app.common.i18n import I18n, get_i18n
from app.common.middleware import current_request_id
from app.common.validation import field_errors, is_malformed_json
from app.infrastructure.database.errors import find_unique_violation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from fastapi import FastAPI, Request
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.http")


def i18n_of(app: Any) -> I18n:
    i18n = getattr(getattr(app, "state", None), "i18n", None)
    return i18n if isinstance(i18n, I18n) else get_i18n()


def _errors_data(i18n: I18n, errors: Sequence[FieldError]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for error in errors:
        params = dict(error.params) if error.params else None
        item: dict[str, Any] = {
            "field": error.field,
            "rule": error.rule,
            "message": error.message or i18n.rule_message(error.rule, params),
        }
        if params:
            item["params"] = params
        items.append(item)
    return {"errors": items}


def error_response(
    i18n: I18n,
    error_key: str,
    *,
    data: Any = None,
    message: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    spec = ERRORS[error_key]
    body = {
        "code": spec.code,
        "message": message or i18n.error_message(error_key),
        "errorKey": error_key,
        "data": data,
        "requestId": current_request_id() or "",
    }
    merged = dict(headers or {})
    if spec.status == 401:
        merged.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(body, status_code=spec.status, headers=merged)


def render_error(i18n: I18n, exc: BaseException) -> JSONResponse | None:
    """已知异常转为错误响应；未知异常返回 None。"""
    if isinstance(exc, ValidationFailed):
        return error_response(i18n, exc.error_key, data=_errors_data(i18n, exc.errors))
    if isinstance(exc, AppError):
        return error_response(
            i18n, exc.error_key, data=exc.data, message=exc.message, headers=exc.headers
        )
    if isinstance(exc, RequestValidationError):
        errors = exc.errors()
        if is_malformed_json(errors):
            return error_response(i18n, "common.malformedJson")
        return render_error(i18n, ValidationFailed(field_errors(errors)))
    if isinstance(exc, StarletteHTTPException):
        return error_response(
            i18n, error_key_for_status(exc.status_code), headers=exc.headers or None
        )
    if isinstance(exc, IntegrityError):
        violation = find_unique_violation(exc)
        if violation is not None:
            return error_response(i18n, violation.error_key)
    return None


def _log(request_path: str, response: JSONResponse, exc: BaseException) -> None:
    extra = {"path": request_path, "request_id": current_request_id()}
    if response.status_code >= 500:
        logger.error("%s: %s", type(exc).__name__, exc, extra=extra, exc_info=exc)
    else:
        logger.warning("%s %s", response.status_code, type(exc).__name__, extra=extra)


def internal_error(i18n: I18n, path: str, exc: BaseException) -> JSONResponse:
    response = error_response(i18n, "common.internalError")
    _log(path, response, exc)
    return response


def allowed_methods(app: Any, scope: Scope) -> str:
    """同一路径下所有路由允许的方法，GET 隐含 HEAD。"""
    methods: set[str] = set()
    for route in getattr(app, "routes", ()):
        route_methods = getattr(route, "methods", None)
        if route_methods and route.matches(scope)[0] is not Match.NONE:
            methods |= set(route_methods)
    if "GET" in methods:
        methods.add("HEAD")
    return ", ".join(sorted(methods))


def register_exception_handlers(app: FastAPI) -> None:
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        i18n = i18n_of(request.app)
        if isinstance(exc, StarletteHTTPException) and exc.status_code == 405:
            exc.headers = {
                **(exc.headers or {}),
                "Allow": allowed_methods(request.app, request.scope),
            }
        response = render_error(i18n, exc)
        if response is None:
            return internal_error(i18n, request.url.path, exc)
        _log(request.url.path, response, exc)
        return response

    for exc_class in (AppError, RequestValidationError, StarletteHTTPException, IntegrityError):
        app.add_exception_handler(exc_class, handle)


class CatchAllMiddleware:
    """异常处理器未接住的异常转为 500 `common.internalError`，响应不含异常信息。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            if started:
                raise
            i18n = i18n_of(scope.get("app"))
            response = render_error(i18n, exc) or internal_error(i18n, scope.get("path", ""), exc)
            await response(scope, receive, send)
