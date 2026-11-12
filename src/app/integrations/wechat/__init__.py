"""微信：小程序 code2session、APP OAuth、小程序手机号、订阅消息（nestjs WechatService）。"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Final

import httpx

from app.common.exceptions import HttpError, unprocessable
lazy from app.config import WechatSettings
lazy from app.infrastructure.cache import Cache

__all__ = [
    "WechatApiError",
    "WechatPhoneInfo",
    "WechatService",
    "WechatSubscribeMessage",
    "WechatUserInfo",
]

logger = logging.getLogger("app.wechat")

API_BASE: Final = "https://api.weixin.qq.com"
TIMEOUT_S: Final = 10.0
MINI_APP_ACCESS_TOKEN_CACHE_KEY: Final = "wechat:mini_app:access_token"  # noqa: S105
ACCESS_TOKEN_INVALID_ERROR_CODES: Final = frozenset({40001, 40014, 42001})
PHONE_CODE_INVALID_ERROR_CODES: Final = frozenset({40029, 40163})


class WechatApiError(Exception):
    def __init__(self, errcode: int, errmsg: str) -> None:
        super().__init__(f"WeChat API error {errcode}: {errmsg}")
        self.errcode = errcode
        self.errmsg = errmsg


@dataclass(frozen=True, slots=True)
class WechatUserInfo:
    open_id: str | None
    union_id: str | None = None
    session_key: str | None = None
    nickname: str | None = None
    avatar: str | None = None
    gender: int | None = None


@dataclass(frozen=True, slots=True)
class WechatSubscribeMessage:
    open_id: str
    template_id: str
    data: dict[str, str]
    page: str | None = None


@dataclass(frozen=True, slots=True)
class WechatPhoneInfo:
    pure_phone_number: str
    country_code: str


def _wechat_error(field: str, error: str, wechat_error: Any = None) -> HttpError:
    errors: dict[str, Any] = {field: error}
    if wechat_error is not None:
        errors["wechatError"] = wechat_error
    return unprocessable({"status": 422, "errors": errors})


def _not_configured() -> HttpError:
    return _wechat_error("wechat", "notConfigured")


def _request_failed() -> HttpError:
    return _wechat_error("wechat", "requestFailed")


class WechatService:
    def __init__(
        self,
        settings: WechatSettings,
        cache: Cache,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.transport = transport

    async def _fetch(
        self,
        url: str,
        label: str,
        *,
        params: dict[str, str] | None = None,
        body: Any = None,
    ) -> Any:
        """GET（或带 body 时 JSON POST）微信接口；非 2xx 为 `wechat: requestFailed`。"""
        async with (
            asyncio.timeout(TIMEOUT_S),
            httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT_S) as client,
        ):
            if body is None:
                response = await client.get(url, params=params)
            else:
                response = await client.post(url, params=params, json=body)
            if response.is_error:
                logger.error(
                    "%s HTTP error: %s %s", label, response.status_code, response.reason_phrase
                )
                raise _request_failed()
            return response.json()

    async def mini_app_login(self, code: str) -> WechatUserInfo:
        return await self._code2session(
            self.settings.mini_app_id,
            self.settings.mini_app_secret.get_secret_value(),
            code,
            "WeChat Mini App",
        )

    async def _code2session(
        self, app_id: str, app_secret: str, code: str, label: str
    ) -> WechatUserInfo:
        if not app_id or not app_secret:
            logger.error("%s credentials not configured", label)
            raise _not_configured()
        params = {
            "appid": app_id,
            "secret": app_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        }
        try:
            data = await self._fetch(
                f"{API_BASE}/sns/jscode2session", f"{label} login", params=params
            )
            if data.get("errcode"):
                logger.error("%s login failed: %s - %s", label, data["errcode"], data.get("errmsg"))
                raise _wechat_error("code", "invalidCode", data.get("errmsg"))
        except HttpError:
            raise
        except Exception:
            logger.exception("%s login request failed", label)
            raise _request_failed() from None
        logger.info("%s login successful for openid: %s", label, data.get("openid"))
        return WechatUserInfo(
            open_id=data.get("openid"),
            union_id=data.get("unionid"),
            session_key=data.get("session_key"),
        )

    async def app_login(self, code: str) -> WechatUserInfo:
        app_id = self.settings.app_id
        app_secret = self.settings.app_secret.get_secret_value()
        if not app_id or not app_secret:
            logger.error("WeChat App credentials not configured")
            raise _not_configured()
        params = {
            "appid": app_id,
            "secret": app_secret,
            "code": code,
            "grant_type": "authorization_code",
        }
        try:
            token = await self._fetch(
                f"{API_BASE}/sns/oauth2/access_token", "WeChat App token", params=params
            )
            if token.get("errcode"):
                logger.error(
                    "WeChat App token exchange failed: %s - %s",
                    token["errcode"],
                    token.get("errmsg"),
                )
                raise _wechat_error("code", "invalidCode", token.get("errmsg"))
            info = await self._fetch(
                f"{API_BASE}/sns/userinfo",
                "WeChat App user info",
                params={"access_token": token.get("access_token"), "openid": token.get("openid")},
            )
            if info.get("errcode"):
                logger.error(
                    "WeChat App user info failed: %s - %s", info["errcode"], info.get("errmsg")
                )
                raise _wechat_error("wechat", "userInfoFailed", info.get("errmsg"))
        except HttpError:
            raise
        except Exception:
            logger.exception("WeChat App login request failed")
            raise _request_failed() from None
        logger.info("WeChat App login successful for openid: %s", token.get("openid"))
        union_id = info.get("unionid")
        return WechatUserInfo(
            open_id=token.get("openid"),
            union_id=union_id if union_id is not None else token.get("unionid"),
            nickname=info.get("nickname"),
            avatar=info.get("headimgurl"),
            gender=info.get("sex"),
        )

    async def get_mini_app_access_token(self) -> str:
        cached = await self.cache.get(MINI_APP_ACCESS_TOKEN_CACHE_KEY)
        if cached:
            return str(cached)
        return await self._fetch_stable_token(force_refresh=False)

    async def _refresh_mini_app_access_token(self, invalid_token: str) -> str:
        await self.cache.delete(MINI_APP_ACCESS_TOKEN_CACHE_KEY)
        token = await self._fetch_stable_token(force_refresh=False)
        if token == invalid_token:
            return await self._fetch_stable_token(force_refresh=True)
        return token

    async def _fetch_stable_token(self, *, force_refresh: bool) -> str:
        app_id = self.settings.mini_app_id
        app_secret = self.settings.mini_app_secret.get_secret_value()
        if not app_id or not app_secret:
            logger.error("WeChat Mini App credentials not configured")
            raise _not_configured()
        data = await self._fetch(
            f"{API_BASE}/cgi-bin/stable_token",
            "Get stable_token",
            body={
                "grant_type": "client_credential",
                "appid": app_id,
                "secret": app_secret,
                "force_refresh": force_refresh,
            },
        )
        if data.get("errcode"):
            logger.error("Get stable_token failed: %s - %s", data["errcode"], data.get("errmsg"))
            raise WechatApiError(data["errcode"], data.get("errmsg") or "")
        expires_in = int(data.get("expires_in") or 0)
        ttl = expires_in - 300 if expires_in > 600 else expires_in // 2
        token = str(data.get("access_token"))
        if ttl > 0:
            await self.cache.set(MINI_APP_ACCESS_TOKEN_CACHE_KEY, token, ttl * 1000)
        return token

    async def _call_mini_app_api(self, path: str, body: Any, label: str) -> dict[str, Any]:
        """以小程序 access_token 调用接口，token 失效时刷新并重试一次。"""

        async def request(access_token: str) -> dict[str, Any]:
            data: dict[str, Any] = await self._fetch(
                f"{API_BASE}{path}", label, params={"access_token": access_token}, body=body
            )
            return data

        access_token = await self.get_mini_app_access_token()
        data = await request(access_token)
        if data.get("errcode") in ACCESS_TOKEN_INVALID_ERROR_CODES:
            logger.warning("%s: access_token invalid (%s), refreshing", label, data["errcode"])
            data = await request(await self._refresh_mini_app_access_token(access_token))
        if data.get("errcode"):
            raise WechatApiError(data["errcode"], data.get("errmsg") or "")
        return data

    async def get_phone_number(self, code: str) -> WechatPhoneInfo:
        try:
            data = await self._call_mini_app_api(
                "/wxa/business/getuserphonenumber", {"code": code}, "Get phone number"
            )
            phone_info = data.get("phone_info") or {}
            if not phone_info.get("purePhoneNumber"):
                raise WechatApiError(-1, "phone_info missing")
        except HttpError:
            raise
        except Exception as error:
            logger.error("Get phone number failed: %s", error)
            if (
                isinstance(error, WechatApiError)
                and error.errcode in PHONE_CODE_INVALID_ERROR_CODES
            ):
                raise unprocessable(
                    {
                        "status": 422,
                        "error": "invalidPhoneCode",
                        "errors": {"phoneCode": "invalidCode"},
                    }
                ) from None
            raise _request_failed() from None
        return WechatPhoneInfo(
            pure_phone_number=phone_info["purePhoneNumber"],
            country_code=str(phone_info.get("countryCode")),
        )

    async def send_subscribe_message(self, message: WechatSubscribeMessage) -> None:
        """发送小程序订阅消息；微信返回非 0 errcode（如 43101 用户未订阅）时抛 WechatApiError。"""
        body: dict[str, Any] = {"touser": message.open_id, "template_id": message.template_id}
        if message.page is not None:
            body["page"] = message.page
        body["data"] = {key: {"value": value} for key, value in message.data.items()}
        body["miniprogram_state"] = self.settings.mini_program_state.value
        body["lang"] = "zh_CN"
        await self._call_mini_app_api(
            "/cgi-bin/message/subscribe/send", body, "Send subscribe message"
        )
