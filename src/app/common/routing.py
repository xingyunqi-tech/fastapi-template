"""ApiRouter.endpoint：operationId、鉴权、状态码、信封、权限、限流、审计、幂等键、上传。"""

import asyncio
import email.message
import functools
import inspect
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Final, get_args, get_origin

from fastapi import Depends, FastAPI, Request, params
from fastapi.dependencies.utils import get_flat_params
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
lazy from starlette.routing import Match

from app.common.envelope import (
    ErrorResponse,
    ValidationErrorResponse,
    envelope_model,
    success,
)
from app.common.errors import AppError
from app.common.middleware import limit_body
lazy from app.common.handlers import i18n_of, render_error
lazy from app.common.idempotency import IdempotentReplay, claim_of, idempotency_guard
lazy from app.infrastructure.audit import Audit, record_audit
lazy from app.infrastructure.throttler.rules import Rule

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Sequence

    from starlette.types import Receive, Scope, Send

API_PREFIX: Final = "/api"
_AUDIT_REQUEST: Final = "_audit_request"
_OPERATION_ID_RE: Final = re.compile(r"([a-z]+)(?:[A-Z][a-z0-9]*)*")
OPERATION_VERBS: Final = frozenset(
    {
        "bind",
        "change",
        "check",
        "confirm",
        "create",
        "delete",
        "download",
        "forgot",
        "get",
        "list",
        "login",
        "logout",
        "read",
        "refresh",
        "register",
        "resend",
        "reset",
        "revoke",
        "send",
        "set",
        "update",
        "upload",
        "verify",
    }
)
BEARER_SECURITY: Final[list[dict[str, list[str]]]] = [{"bearerAuth": []}]


class Auth(StrEnum):
    PUBLIC = "public"
    JWT = "jwt"
    OPTIONAL_JWT = "optional-jwt"
    MONITORING = "monitoring"


AUTH_DEPENDENCIES: dict[Auth, Callable[..., Any]] = {}
PERMISSION_GUARDS: list[Callable[..., Callable[..., Any]]] = []
RATE_LIMIT_GUARDS: list[Callable[[Request], Awaitable[None]]] = []


def register_auth(
    dependencies: dict[Auth, Callable[..., Any]],
    permission_guard: Callable[..., Callable[..., Any]],
) -> None:
    AUTH_DEPENDENCIES.update(dependencies)
    PERMISSION_GUARDS[:] = [permission_guard]


def register_rate_limit_guard(guard: Callable[[Request], Awaitable[None]]) -> None:
    RATE_LIMIT_GUARDS[:] = [guard]


async def _rate_limit(request: Request) -> None:
    for guard in RATE_LIMIT_GUARDS:
        await guard(request)


@dataclass(frozen=True, slots=True)
class RouteMeta:
    auth: Auth
    envelope: bool = True
    rate_limit: tuple[Rule, ...] | None = None
    """None 为私有默认规则，空元组为不限流。"""
    audit: Audit | None = None
    idempotent: bool = False
    upload: bool = False
    permissions: tuple[str, ...] = ()
    any_permissions: tuple[str, ...] = ()
    rbac: bool = False


@dataclass(slots=True)
class Declaration:
    method: str
    path: str
    endpoint: Callable[..., Any]
    meta: RouteMeta
    operation_id: str
    status: int
    response_model: Any
    summary: str | None
    description: str | None
    include_in_schema: bool
    request_body: dict[str, Any] | None
    parameters: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


_INFER: Final[Any] = object()


def _guards(meta: RouteMeta) -> list[Any]:
    dependencies: list[Any] = [Depends(_rate_limit)]
    if meta.auth is not Auth.PUBLIC:
        dependency = AUTH_DEPENDENCIES.get(meta.auth)
        if dependency is None:
            raise RuntimeError(f"鉴权方式 {meta.auth} 未注册实现")
        dependencies.append(Depends(dependency))
    if meta.rbac or meta.permissions or meta.any_permissions:
        if not PERMISSION_GUARDS:
            raise RuntimeError("权限守卫未注册实现")
        factory = PERMISSION_GUARDS[0]
        if meta.any_permissions:
            dependencies.append(Depends(factory(list(meta.any_permissions), any_of=True)))
        else:
            dependencies.append(Depends(factory(list(meta.permissions))))
    if meta.idempotent:
        dependencies.append(Depends(idempotency_guard))
    return dependencies


def _is_response_type(annotation: Any) -> bool:
    candidates = get_args(annotation) if get_origin(annotation) is not None else (annotation,)
    return any(inspect.isclass(item) and issubclass(item, Response) for item in candidates)


def _return_annotation(endpoint: Callable[..., Any]) -> Any:
    annotation = inspect.signature(endpoint, eval_str=True).return_annotation
    return None if annotation is inspect.Signature.empty else annotation


def _response_model(declaration: Declaration) -> Any:
    declared = declaration.response_model
    annotation = _return_annotation(declaration.endpoint) if declared is _INFER else declared
    if _is_response_type(annotation):
        return None
    if declaration.meta.envelope:
        return envelope_model(annotation)
    return annotation


def to_plain(result: Any) -> Any:
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json", by_alias=True)
    if isinstance(result, list):
        return [to_plain(item) for item in result]
    if isinstance(result, dict):
        return {key: to_plain(value) for key, value in result.items()}
    return result


def _body_parameter(endpoint: Callable[..., Any]) -> str | None:
    """第一个未标注 Query / Path / Header / Depends 的 Pydantic 模型参数。"""
    for name, parameter in inspect.signature(endpoint, eval_str=True).parameters.items():
        annotation = parameter.annotation
        metadata: tuple[Any, ...] = ()
        if get_origin(annotation) is Annotated:
            annotation, *rest = get_args(annotation)
            metadata = tuple(rest)
        if not (inspect.isclass(annotation) and issubclass(annotation, BaseModel)):
            continue
        if any(isinstance(item, params.Param | params.Depends) for item in metadata):
            continue
        return name
    return None


def _wrap_endpoint(endpoint: Callable[..., Any], meta: RouteMeta) -> Callable[..., Any]:
    is_coroutine = inspect.iscoroutinefunction(endpoint)
    audit = meta.audit
    body_name = _body_parameter(endpoint) if audit is not None else None

    @functools.wraps(endpoint)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        request = kwargs.pop(_AUDIT_REQUEST, None)
        if is_coroutine:
            result = await endpoint(*args, **kwargs)
        else:
            result = await run_in_threadpool(endpoint, *args, **kwargs)
        if audit is not None and request is not None:
            body = kwargs.get(body_name) if body_name else None
            request.state.audit = BackgroundTask(
                record_audit,
                request,
                audit,
                body.model_dump(mode="json", by_alias=True, exclude_unset=True)
                if isinstance(body, BaseModel)
                else None,
                None if isinstance(result, Response) else to_plain(result),
            )
        if isinstance(result, Response) or not meta.envelope:
            return result
        return success(result)

    if audit is not None:
        signature = inspect.signature(endpoint)
        extra = inspect.Parameter(
            _AUDIT_REQUEST, inspect.Parameter.KEYWORD_ONLY, annotation=Request
        )
        wrapped.__signature__ = signature.replace(  # type: ignore[attr-defined]
            parameters=[*signature.parameters.values(), extra]
        )
    return wrapped


def _is_json(content_type: str) -> bool:
    message = email.message.Message()
    message["content-type"] = content_type
    subtype = message.get_content_subtype()
    return message.get_content_maintype() == "application" and (
        subtype == "json" or subtype.endswith("+json")
    )


def _reject_constant(value: str) -> Any:
    raise ValueError(f"{value} is not valid JSON")


class ApiRoute(APIRoute):
    def __init__(
        self, path: str, endpoint: Callable[..., Any], *, meta: RouteMeta, **kwargs: Any
    ) -> None:
        self.meta = meta
        self.original_endpoint = endpoint
        super().__init__(path, _wrap_endpoint(endpoint, meta), **kwargs)

    @property
    def auth(self) -> Auth:
        return self.meta.auth

    @property
    def rate_limit(self) -> tuple[Rule, ...] | None:
        return self.meta.rate_limit

    async def _check_json_body(self, request: Request) -> None:
        """JSON 请求体：非 JSON `Content-Type` 为 415，非 UTF-8 或含 NaN / Infinity 为 400。"""
        body_field = self.body_field
        if body_field is None or isinstance(body_field.field_info, params.Form):
            return
        body = await request.body()
        if not body:
            return
        content_type = request.headers.get("content-type")
        if not content_type or not _is_json(content_type):
            raise AppError("common.unsupportedMediaType")
        try:
            json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
        except UnicodeDecodeError, ValueError:
            raise AppError("common.malformedJson") from None

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        idempotent = self.meta.idempotent

        async def app(request: Request) -> Response:
            await self._check_json_body(request)
            if not idempotent:
                response = await handler(request)
            else:
                response = await _run_idempotent(handler, request)
            task = getattr(request.state, "audit", None)
            if isinstance(task, BackgroundTask) and response.background is None:
                response.background = task
            return response

        return app

    def _as_get(self, scope: Scope) -> Scope:
        if scope.get("method") == "HEAD" and self.methods and "GET" in self.methods:
            return {**scope, "method": "GET"}
        return scope

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        """GET 路由同时响应 HEAD。"""
        return super().matches(self._as_get(scope))

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope = self._as_get(scope)
        if not self.meta.upload and (not self.methods or scope["method"] in self.methods):
            limit = int(scope["app"].state.settings.app.body_limit)
            receive = limit_body(scope, receive, limit)
        await super().handle(scope, receive, send)


async def _run_idempotent(
    handler: Callable[[Request], Awaitable[Response]], request: Request
) -> Response:
    try:
        response = await handler(request)
    except IdempotentReplay as replay:
        return replay.response
    except asyncio.CancelledError:
        claim = claim_of(request)
        if claim is not None:
            await asyncio.shield(claim.release())
        raise
    except Exception as exc:
        claim = claim_of(request)
        if claim is None:
            raise
        rendered = render_error(i18n_of(request.app), exc)
        if rendered is None:
            await claim.release()
            raise
        await claim.finish(rendered)
        return rendered
    claim = claim_of(request)
    if claim is not None:
        await claim.finish(response)
    return response


class ApiRouter:
    """路由声明；`rbac=True` 时所有路由经过权限守卫。由 `include_routes` 注册到应用。"""

    def __init__(self, *, prefix: str = "", tags: Sequence[str] = (), rbac: bool = False) -> None:
        self.prefix = prefix
        self.tags = list(tags)
        self.rbac = rbac
        self.declarations: list[Declaration] = []

    def endpoint(
        self,
        method: str,
        path: str,
        *,
        operation_id: str,
        auth: Auth,
        status: int = 200,
        envelope: bool = True,
        response_model: Any = _INFER,
        summary: str | None = None,
        description: str | None = None,
        include_in_schema: bool = True,
        permissions: Sequence[str] = (),
        any_permissions: Sequence[str] = (),
        rate_limit: Sequence[Rule] | None = None,
        audit: Audit | None = None,
        idempotent: bool = False,
        upload: bool = False,
        request_body: dict[str, Any] | None = None,
        parameters: Sequence[dict[str, Any]] = (),
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """`request_body`、`parameters` 为手写的 OpenAPI 片段，用于不经 FastAPI 解析的上传接口。"""
        meta = RouteMeta(
            auth=auth,
            envelope=envelope,
            rate_limit=None if rate_limit is None else tuple(rate_limit),
            audit=audit,
            idempotent=idempotent,
            upload=upload,
            permissions=tuple(permissions),
            any_permissions=tuple(any_permissions),
            rbac=self.rbac,
        )

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.declarations.append(
                Declaration(
                    method=method,
                    path=self.prefix + path,
                    endpoint=fn,
                    meta=meta,
                    operation_id=operation_id,
                    status=status,
                    response_model=response_model,
                    summary=summary,
                    description=description,
                    include_in_schema=include_in_schema,
                    request_body=request_body,
                    parameters=list(parameters),
                    tags=list(self.tags),
                )
            )
            return fn

        return decorator


def _error_responses(meta: RouteMeta, validates: bool) -> dict[int | str, dict[str, Any]]:
    responses: dict[int | str, dict[str, Any]] = {}

    def add(status: int | str, description: str, model: type[BaseModel] = ErrorResponse) -> None:
        responses[status] = {"model": model, "description": description}

    if meta.auth is Auth.JWT:
        add(401, "未认证")
    if meta.auth is Auth.MONITORING or meta.rbac or meta.permissions or meta.any_permissions:
        add(403, "无权限")
    if meta.idempotent:
        add(409, "幂等键对应的请求仍在处理")
    if meta.upload:
        add(413, "文件过大")
    if validates:
        add(422, "校验失败", ValidationErrorResponse)
    add("default", "错误")
    return responses


def _openapi_extra(declaration: Declaration) -> dict[str, Any] | None:
    extra: dict[str, Any] = {}
    auth = declaration.meta.auth
    if auth in {Auth.JWT, Auth.MONITORING}:
        extra["security"] = BEARER_SECURITY
    elif auth is Auth.OPTIONAL_JWT:
        extra["security"] = [{}, *BEARER_SECURITY]
    if declaration.request_body is not None:
        extra["requestBody"] = declaration.request_body
    if declaration.parameters:
        extra["parameters"] = declaration.parameters
    return extra or None


def _build_route(
    declaration: Declaration, prefix: str, responses: dict[int | str, dict[str, Any]]
) -> ApiRoute:
    return ApiRoute(
        prefix + declaration.path,
        declaration.endpoint,
        meta=declaration.meta,
        methods=[declaration.method],
        name=declaration.endpoint.__name__,
        operation_id=declaration.operation_id,
        status_code=declaration.status,
        response_model=_response_model(declaration),
        responses=responses,
        summary=declaration.summary,
        description=declaration.description,
        tags=declaration.tags,
        dependencies=_guards(declaration.meta),
        include_in_schema=declaration.include_in_schema,
        openapi_extra=_openapi_extra(declaration),
    )


def _validates(route: ApiRoute) -> bool:
    return bool(
        route.body_field is not None
        or get_flat_params(route.dependant)
        or (
            route.openapi_extra
            and ("requestBody" in route.openapi_extra or "parameters" in route.openapi_extra)
        )
    )


class RouteDeclarationError(RuntimeError):
    pass


def include_routes(app: FastAPI, router: ApiRouter, prefix: str = "") -> None:
    for declaration in router.declarations:
        route = _build_route(declaration, prefix, _error_responses(declaration.meta, False))
        if _validates(route):
            route = _build_route(declaration, prefix, _error_responses(declaration.meta, True))
        app.router.routes.append(route)


def check_operation_id(operation_id: str) -> str | None:
    match = _OPERATION_ID_RE.fullmatch(operation_id)
    if match is None:
        return "不是 lowerCamelCase"
    if match.group(1) not in OPERATION_VERBS:
        return f"未以动词开头（{match.group(1)}）"
    return None


def finalize_routes(app: FastAPI) -> None:
    """API 路由须由 ApiRouter 声明；文档内 operationId 唯一、lowerCamelCase、动词开头。"""
    problems: list[str] = []
    seen: dict[str, str] = {}
    for route in app.router.routes:
        if not isinstance(route, APIRoute):
            continue
        label = f"{','.join(sorted(route.methods or ()))} {route.path}"
        if not isinstance(route, ApiRoute):
            problems.append(f"{label} 未通过 ApiRouter 声明")
            continue
        if not route.include_in_schema or route.operation_id is None:
            continue
        reason = check_operation_id(route.operation_id)
        if reason is not None:
            problems.append(f"{label} 的 operationId {route.operation_id} {reason}")
        elif route.operation_id in seen and seen[route.operation_id] != label:
            problems.append(
                f"operationId {route.operation_id} 重复：{seen[route.operation_id]}、{label}"
            )
        seen.setdefault(route.operation_id, label)
    if problems:
        raise RouteDeclarationError("路由声明错误：" + "；".join(problems))
