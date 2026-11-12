"""ApiRoute：统一信封、默认 200、鉴权声明；以及 Express 风格的路由匹配。"""

import functools
import inspect
import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from fastapi.datastructures import Default
from fastapi.routing import APIRoute, APIRouter
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response
from starlette.routing import BaseRoute, Match, Route

from app.common.exceptions import HttpError
from app.common.responses import JsonResponse, envelope, is_api_response

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fastapi import FastAPI
    from starlette.types import Receive, Scope, Send

API_META_KEY: Final = "x-api"
INFER_RESPONSE_MODEL: Any = Default(None)
_DECODE_ERROR_KEY: Final = "app.param_decode_error"
_PERCENT_RE: Final = re.compile(r"%(?![0-9A-Fa-f]{2})")


class Auth(StrEnum):
    PUBLIC = "public"
    JWT = "jwt"
    OPTIONAL_JWT = "optional-jwt"
    REFRESH_JWT = "refresh-jwt"
    MONITORING = "monitoring"


def to_plain(result: Any) -> Any:
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json", by_alias=True)
    if isinstance(result, list):
        return [to_plain(item) for item in result]
    if isinstance(result, dict):
        return {key: to_plain(value) for key, value in result.items()}
    return result


def _is_void(endpoint: Callable[..., Any]) -> bool:
    annotation = inspect.signature(endpoint, eval_str=True).return_annotation
    return annotation is None or annotation is type(None)


def _wrap_endpoint(
    endpoint: Callable[..., Any], status_code: int, wrap: bool
) -> Callable[..., Any]:
    is_coroutine = inspect.iscoroutinefunction(endpoint)
    void = _is_void(endpoint)

    @functools.wraps(endpoint)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        if is_coroutine:
            result = await endpoint(*args, **kwargs)
        else:
            result = await run_in_threadpool(endpoint, *args, **kwargs)
        if isinstance(result, Response):
            return result
        payload = to_plain(result)
        if wrap and not is_api_response(payload):
            payload = envelope(payload, include_data=not void)
        return JsonResponse(payload, status_code=status_code)

    return wrapped


def decode_uri_component(value: str) -> str:
    """JS decodeURIComponent：非法转义或非法 UTF-8 抛 ValueError。"""
    if "%" not in value:
        return value
    if _PERCENT_RE.search(value):
        raise ValueError(value)
    raw = value.encode("latin-1", errors="strict")
    parts = re.split(rb"(%[0-9A-Fa-f]{2})", raw)
    decoded = b"".join(
        bytes([int(part[1:], 16)]) if part.startswith(b"%") and len(part) == 3 else part
        for part in parts
    )
    return decoded.decode("utf-8", errors="strict")


class ApiRoute(APIRoute):
    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        extra = kwargs.get("openapi_extra") or {}
        meta = extra.get(API_META_KEY) or {}
        self.auth: Auth | None = meta.get("auth")
        self.wrap: bool = meta.get("wrap", True)
        if kwargs.get("status_code") is None:
            kwargs["status_code"] = 200
        wrapped = _wrap_endpoint(endpoint, kwargs["status_code"], self.wrap)
        super().__init__(path, wrapped, **kwargs)
        self.endpoint = endpoint

    def _as_get(self, scope: Scope) -> Scope:
        if scope.get("method") == "HEAD" and self.methods and "GET" in self.methods:
            return {**scope, "method": "GET"}
        return scope

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child = super().matches(self._as_get(scope))
        params = child.get("path_params")
        if match is not Match.NONE and params:
            decoded: dict[str, Any] = {}
            for key, value in params.items():
                try:
                    decoded[key] = decode_uri_component(value)
                except ValueError:
                    child[_DECODE_ERROR_KEY] = value
                    decoded[key] = value
            child["path_params"] = decoded
        return match, child

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        failed = scope.get(_DECODE_ERROR_KEY)
        if failed is not None:
            raise HttpError(400, f"Failed to decode param '{failed}'")
        await super().handle(self._as_get(scope), receive, send)


class ApiRouter(APIRouter):
    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("route_class", ApiRoute)
        super().__init__(**kwargs)

    def endpoint(
        self,
        method: str,
        path: str,
        *,
        operation_id: str,
        auth: Auth,
        status_code: int = 200,
        wrap: bool = True,
        response_model: Any = INFER_RESPONSE_MODEL,
        summary: str | None = None,
        include_in_schema: bool = True,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(
                path,
                fn,
                methods=[method],
                operation_id=operation_id,
                status_code=status_code,
                response_model=response_model,
                summary=summary,
                include_in_schema=include_in_schema,
                openapi_extra={API_META_KEY: {"auth": auth, "wrap": wrap}},
            )
            return fn

        return decorator


class RouteDeclarationError(RuntimeError):
    pass


def include_routes(app: FastAPI, router: ApiRouter, prefix: str = "") -> None:
    """把 ApiRouter 的路由按前缀直接注册到 app，保持 ApiRoute 的包装与匹配规则。"""
    for route in router.routes:
        if not isinstance(route, ApiRoute):
            raise RouteDeclarationError(f"{route} 不是 ApiRoute")
        app.router.routes.append(
            ApiRoute(
                prefix + route.path,
                route.endpoint,
                methods=sorted(route.methods or ()),
                name=route.name,
                operation_id=route.operation_id,
                status_code=route.status_code,
                response_model=route.response_model,
                response_class=route.response_class,
                response_description=route.response_description,
                responses=route.responses,
                summary=route.summary,
                description=route.description,
                deprecated=route.deprecated,
                tags=list(route.tags),
                dependencies=list(route.dependencies),
                include_in_schema=route.include_in_schema,
                openapi_extra=route.openapi_extra,
            )
        )


_EXPRESS_FLAG: Final = "_express_matching"


def _express_regex(route: Route | APIRoute) -> re.Pattern[str]:
    pattern = route.path_regex.pattern
    if route.path == "/":
        return re.compile(pattern, re.IGNORECASE)
    body = pattern.removesuffix("$")
    return re.compile(body.removesuffix("/") + "/?$", re.IGNORECASE)


def finalize_routes(app: FastAPI) -> None:
    """校验每个 API 路由都声明了鉴权方式，并改为不区分大小写、尾斜杠可选的匹配。"""
    undeclared: list[str] = []
    routes: Sequence[BaseRoute] = app.router.routes
    for route in routes:
        if isinstance(route, APIRoute) and (not isinstance(route, ApiRoute) or route.auth is None):
            undeclared.append(f"{','.join(sorted(route.methods or ()))} {route.path}")
        if isinstance(route, Route | APIRoute) and not getattr(route, _EXPRESS_FLAG, False):
            route.path_regex = _express_regex(route)
            setattr(route, _EXPRESS_FLAG, True)
    if undeclared:
        raise RouteDeclarationError("以下路由未声明鉴权方式：" + "；".join(undeclared))
