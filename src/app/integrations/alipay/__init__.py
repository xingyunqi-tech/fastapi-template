"""支付宝小程序登录：alipay.system.oauth.token 用授权码换取 open_id（nestjs AlipayService）。"""

import asyncio
import base64
import logging
import textwrap
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.common.exceptions import HttpError, unprocessable
from app.config.alipay import AlipaySignType
lazy from app.config import AlipaySettings

__all__ = ["AlipayService", "build_sign_string", "format_private_key"]

logger = logging.getLogger("app.alipay")

TIMEOUT_S: Final = 30.0
BEIJING: Final = timedelta(hours=8)


def _error(field: str, error: str) -> HttpError:
    return unprocessable({"status": 422, "errors": {field: error}})


def to_beijing_time(moment: datetime) -> str:
    return (moment.astimezone(UTC) + BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def build_sign_string(params: dict[str, str]) -> str:
    return "&".join(f"{key}={params[key]}" for key in sorted(params) if params[key] != "")


def format_private_key(private_key: str) -> str:
    if "BEGIN PRIVATE KEY" in private_key:
        return private_key
    body = "\n".join(textwrap.wrap("".join(private_key.split()), 64))
    return f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----"


class AlipayService:
    def __init__(
        self, settings: AlipaySettings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    def sign(self, params: dict[str, str]) -> str:
        key = serialization.load_pem_private_key(
            format_private_key(self.settings.private_key.get_secret_value()).encode(),
            password=None,
        )
        if not isinstance(key, rsa.RSAPrivateKey):
            raise TypeError("ALIPAY_PRIVATE_KEY must be an RSA private key")
        algorithm: hashes.HashAlgorithm = (
            hashes.SHA256() if self.settings.sign_type is AlipaySignType.RSA2 else hashes.SHA1()  # noqa: S303
        )
        signature = key.sign(build_sign_string(params).encode(), padding.PKCS1v15(), algorithm)
        return base64.b64encode(signature).decode()

    async def _send(self, params: dict[str, str]) -> Any:
        async with (
            asyncio.timeout(TIMEOUT_S),
            httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT_S) as client,
        ):
            response = await client.get(
                f"{self.settings.gateway}?{urlencode(params)}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if response.is_error:
            logger.error("Alipay request failed: %s", response.reason_phrase)
            raise RuntimeError("Alipay request failed")
        return response.json()

    async def exchange_auth_code(self, auth_code: str) -> str:
        """返回 open_id；授权码无效为 invalidAlipayAuthCode，未配置或请求失败为 alipay 字段错误。"""
        settings = self.settings
        if not settings.app_id or not settings.private_key.get_secret_value():
            logger.error("Alipay credentials not configured")
            raise _error("alipay", "notConfigured")
        params = {
            "app_id": settings.app_id,
            "method": "alipay.system.oauth.token",
            "format": "JSON",
            "charset": settings.charset,
            "sign_type": settings.sign_type.value,
            "timestamp": to_beijing_time(datetime.now(UTC)),
            "version": "1.0",
            "grant_type": "authorization_code",
            "code": auth_code,
        }
        params["sign"] = self.sign(params)
        try:
            response = await self._send(params)
        except Exception:
            logger.exception("Alipay oauth token request failed")
            raise _error("alipay", "requestFailed") from None

        error_response = response.get("error_response") if isinstance(response, dict) else None
        if error_response:
            sub_code = error_response.get("sub_code")
            logger.error(
                "Alipay oauth token failed: %s %s", error_response.get("code"), sub_code or ""
            )
            if isinstance(sub_code, str) and sub_code.startswith("isv.code-"):
                raise unprocessable(
                    {
                        "status": 422,
                        "error": "invalidAlipayAuthCode",
                        "errors": {"authCode": "invalidCode"},
                    }
                )
            raise _error("alipay", "requestFailed")

        token = response.get("alipay_system_oauth_token_response") or {}
        open_id = token.get("open_id")
        if not open_id:
            logger.error(
                "Alipay oauth token response has no open_id, "
                "check that OpenID is enabled for the app"
            )
            raise _error("alipay", "requestFailed")
        return str(open_id)
