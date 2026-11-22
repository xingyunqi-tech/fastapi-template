"""微信：小程序 `jscode2session` 与手机号、开放平台移动应用 OAuth。"""

import logging
from typing import Any, Final

lazy import httpx

from app.identity.providers import ProviderId, ProviderIdentity, bad_gateway, code_invalid
from app.identity.providers.http import json_object, request
lazy from app.config import WechatSettings
lazy from app.infrastructure.cache import Cache

logger = logging.getLogger("app.providers.wechat")

API_BASE: Final = "https://api.weixin.qq.com"
ACCESS_TOKEN_CACHE_KEY: Final = "wechat:miniapp:access_token"  # noqa: S105
CODE_INVALID_ERRCODES: Final = frozenset({40029, 40163, 42003})
ACCESS_TOKEN_INVALID_ERRCODES: Final = frozenset({40001, 40014, 42001})


def _checked(body: dict[str, Any], label: str) -> dict[str, Any]:
    errcode = body.get("errcode")
    if not errcode:
        return body
    logger.error("%s errcode %s: %s", label, errcode, body.get("errmsg"))
    if errcode in CODE_INVALID_ERRCODES:
        raise code_invalid()
    raise bad_gateway()


def _identity(body: dict[str, Any], label: str) -> ProviderIdentity:
    openid = body.get("openid")
    if not isinstance(openid, str) or not openid:
        logger.error("%s response has no openid", label)
        raise bad_gateway()
    union_id = body.get("unionid")
    return ProviderIdentity(openid, union_id if isinstance(union_id, str) and union_id else None)


class WechatMiniappClient:
    id = ProviderId.WECHAT_MINIAPP

    def __init__(
        self,
        settings: WechatSettings,
        cache: Cache,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.app_id = settings.miniapp_app_id
        self.secret = settings.miniapp_secret.get_secret_value()
        self.cache = cache
        self.transport = transport

    async def _get(self, path: str, params: dict[str, str], label: str) -> dict[str, Any]:
        response = await request(
            self.transport, "GET", f"{API_BASE}{path}", label=label, params=params
        )
        return _checked(json_object(response, label), label)

    async def exchange(self, code: str) -> ProviderIdentity:
        label = "wechat_miniapp jscode2session"
        body = await self._get(
            "/sns/jscode2session",
            {
                "appid": self.app_id,
                "secret": self.secret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
            label,
        )
        return _identity(body, label)

    async def phone_number(self, code: str) -> str:
        """`getPhoneNumber` 的 `code` 换取手机号，返回 E.164。"""
        label = "wechat_miniapp getuserphonenumber"
        token = await self._access_token()
        body = await self._phone_request(token, code, label)
        if body.get("errcode") in ACCESS_TOKEN_INVALID_ERRCODES:
            await self.cache.delete(ACCESS_TOKEN_CACHE_KEY)
            body = await self._phone_request(await self._access_token(force=True), code, label)
        info = _checked(body, label).get("phone_info")
        if not isinstance(info, dict):
            info = {}
        number, country = info.get("purePhoneNumber"), info.get("countryCode")
        if not number or not country:
            logger.error("%s response has no phone_info", label)
            raise bad_gateway()
        return f"+{country}{number}"

    async def _phone_request(self, token: str, code: str, label: str) -> dict[str, Any]:
        response = await request(
            self.transport,
            "POST",
            f"{API_BASE}/wxa/business/getuserphonenumber",
            label=label,
            params={"access_token": token},
            json={"code": code},
        )
        return json_object(response, label)

    async def _access_token(self, *, force: bool = False) -> str:
        """`stable_token` 缓存至过期前 5 分钟。"""
        if not force:
            cached = await self.cache.get(ACCESS_TOKEN_CACHE_KEY)
            if cached:
                return str(cached)
        label = "wechat_miniapp stable_token"
        response = await request(
            self.transport,
            "POST",
            f"{API_BASE}/cgi-bin/stable_token",
            label=label,
            json={
                "grant_type": "client_credential",
                "appid": self.app_id,
                "secret": self.secret,
                "force_refresh": force,
            },
        )
        body = json_object(response, label)
        token = body.get("access_token")
        if body.get("errcode") or not isinstance(token, str):
            logger.error("%s errcode %s: %s", label, body.get("errcode"), body.get("errmsg"))
            raise bad_gateway()
        ttl = int(body.get("expires_in") or 0) - 300
        if ttl > 0:
            await self.cache.set(ACCESS_TOKEN_CACHE_KEY, token, ttl * 1000)
        return token


class WechatAppClient:
    id = ProviderId.WECHAT_APP

    def __init__(
        self, settings: WechatSettings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.app_id = settings.app_app_id
        self.secret = settings.app_secret.get_secret_value()
        self.transport = transport

    async def exchange(self, code: str) -> ProviderIdentity:
        label = "wechat_app oauth2/access_token"
        response = await request(
            self.transport,
            "GET",
            f"{API_BASE}/sns/oauth2/access_token",
            label=label,
            params={
                "appid": self.app_id,
                "secret": self.secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        return _identity(_checked(json_object(response, label), label), label)
