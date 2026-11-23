"""约定 §7 限流：路由依赖与 service 内调用共用 `RateLimiter`，响应中间件写 `X-RateLimit-*`。"""

import hashlib
import logging
import re
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Final

from starlette.datastructures import MutableHeaders
lazy from fastapi import Request
lazy from redis.asyncio import Redis

from app.common.client import client_ip
from app.common.errors import AppError, DependencyUnavailableError
from app.common.routing import ApiRoute, Auth, register_rate_limit_guard
from app.infrastructure.telemetry import record_ratelimit_fallback
from app.infrastructure.throttler.ip import normalize_ip
from app.infrastructure.throttler.rules import Rule, SubjectKind, default_rule
from app.infrastructure.throttler.storage import (
    Decision,
    GcraStore,
    MemoryGcraStore,
    Mode,
    RedisGcraStore,
    key,
)
lazy from app.common.context import AppContext
lazy from app.config import Settings

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "Decision",
    "RateLimitHeadersMiddleware",
    "RateLimitUnavailableError",
    "RateLimiter",
    "account_subject",
    "ip_subject",
    "normalize_ip",
    "session_subject",
    "user_subject",
]

logger = logging.getLogger("app.throttler")

ACCOUNT_DIGEST_LENGTH: Final = 16
_WHITESPACE: Final = re.compile(r"\s+")
_decisions: ContextVar[list[Decision] | None] = ContextVar("rate_limit_decisions", default=None)


class RateLimitUnavailableError(DependencyUnavailableError):
    """Redis 出错时共享规则拒绝请求。"""


def ip_subject(request: Request, settings: Settings) -> str:
    return f"ip:{normalize_ip(client_ip(request, settings.app.trusted_proxies) or '')}"


def user_subject(sub: Any) -> str:
    return f"u:{sub}"


def session_subject(sid: Any) -> str:
    return f"s:{sid}"


def account_subject(identifier: str) -> str:
    """`identifier` 为规范化后的邮箱或 E.164 手机号。"""
    digest = hashlib.sha256(identifier.encode()).hexdigest()
    return f"a:{digest[:ACCOUNT_DIGEST_LENGTH]}"


class RateLimiter:
    def __init__(self, redis: Redis | None) -> None:
        self.store: GcraStore = RedisGcraStore(redis) if redis is not None else MemoryGcraStore()

    async def _consume(self, rule: Rule, subject: str, mode: Mode) -> Decision:
        try:
            return await self.store.consume(key(rule.name, subject), rule.quota, mode)
        except Exception as error:
            reason = str(error) or type(error).__name__
            if rule.shared:
                logger.error("Rate limit store unavailable for %s: %s", rule.name, reason)
                raise RateLimitUnavailableError(reason) from error
            logger.warning("Rate limit store unavailable, allowing %s: %s", rule.name, reason)
            record_ratelimit_fallback(rule.name)
            return Decision(True, rule.quota.burst, rule.quota.burst, 0, 0)

    async def check(self, rule: Rule, subject: str, *, mode: Mode = Mode.HIT) -> Decision:
        """计数（`peek` 时只判断）；超限时 429 `common.tooManyRequests`。"""
        decision = await self._consume(rule, subject, mode)
        pending = _decisions.get()
        if pending is not None:
            pending.append(decision)
        if not decision.allowed:
            raise AppError(
                "common.tooManyRequests",
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
        return decision

    async def record_failure(self, rule: Rule, subject: str) -> None:
        """只计失败的规则：本次失败计数，超限不改变本次响应。"""
        try:
            await self.store.consume(key(rule.name, subject), rule.quota, Mode.HIT)
        except Exception as error:
            logger.warning("Failed to count %s failure: %s", rule.name, error)

    async def reset(self, rule: Rule, subject: str) -> None:
        try:
            await self.store.reset(key(rule.name, subject))
        except Exception as error:
            logger.warning("Failed to reset %s: %s", rule.name, error)


def _bearer_sub(request: Request, context: AppContext) -> Any:
    parts = _WHITESPACE.split((request.headers.get("authorization") or "").strip(), maxsplit=2)
    if parts[0].lower() != "bearer" or len(parts) < 2 or not parts[1]:
        return None
    return context.verifier.subject(parts[1])


def subject_for(request: Request, route: ApiRoute, rule: Rule, context: AppContext) -> str:
    if rule.subject is not SubjectKind.IP and route.auth is not Auth.PUBLIC:
        sub = _bearer_sub(request, context)
        if sub is not None:
            return user_subject(sub)
    return ip_subject(request, context.settings)


def route_rules(route: ApiRoute, context: AppContext) -> tuple[Rule, ...]:
    """未声明 `rate_limit` 的路由使用私有默认规则。"""
    if route.rate_limit is not None:
        return route.rate_limit
    settings = context.settings
    return (default_rule(settings.telemetry.service_name, settings.rate_limit.default),)


async def rate_limit_guard(request: Request) -> None:
    route = request.scope.get("route")
    if not isinstance(route, ApiRoute):
        return
    context: AppContext = request.app.state.context
    for rule in route_rules(route, context):
        await context.rate_limiter.check(rule, subject_for(request, route, rule, context))


register_rate_limit_guard(rate_limit_guard)


class RateLimitHeadersMiddleware:
    """取剩余最少的一条判定写 `X-RateLimit-*`；429 只带 `Retry-After`。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        pending: list[Decision] = []
        token = _decisions.set(pending)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start" and pending and message["status"] != 429:
                decision = min(pending, key=lambda item: item.remaining)
                headers = MutableHeaders(scope=message)
                headers["X-RateLimit-Limit"] = str(decision.limit)
                headers["X-RateLimit-Remaining"] = str(decision.remaining)
                headers["X-RateLimit-Reset"] = str(decision.reset_seconds)
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            _decisions.reset(token)
