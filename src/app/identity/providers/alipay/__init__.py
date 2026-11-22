"""支付宝：`alipay.system.oauth.token` 用 `auth_code` 换取 `open_id`，校验响应签名。"""

import base64
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlencode

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
lazy import httpx

from app.identity.providers import ProviderId, ProviderIdentity, bad_gateway, code_invalid
from app.identity.providers.alipay.keys import load_private_key, load_public_key
from app.identity.providers.http import request
lazy from app.config import AlipaySettings
lazy from app.config.alipay import AlipaySignType

logger = logging.getLogger("app.providers.alipay")

METHOD: Final = "alipay.system.oauth.token"
RESPONSE_KEY: Final = "alipay_system_oauth_token_response"
BEIJING: Final = timedelta(hours=8)


def to_beijing_time(moment: datetime) -> str:
    return (moment.astimezone(UTC) + BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def build_sign_string(params: dict[str, str]) -> str:
    return "&".join(f"{key}={params[key]}" for key in sorted(params) if params[key] != "")


def raw_member(text: str, key: str) -> str | None:
    """响应 JSON 中 `key` 对应值的原文（验签内容）。"""
    marker = f'"{key}"'
    start = text.find(marker)
    if start < 0:
        return None
    colon = text.find(":", start + len(marker))
    if colon < 0:
        return None
    index = colon + 1
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    try:
        _, end = json.JSONDecoder().raw_decode(text, index)
    except ValueError:
        return None
    return text[index:end]


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """响应 JSON 含重复键时拒绝。"""
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate key")
    return result


class AlipayClient:
    id = ProviderId.ALIPAY

    def __init__(
        self, settings: AlipaySettings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.private_key = load_private_key(settings.private_key.get_secret_value())
        self.public_key = load_public_key(settings.public_key)
        self.hash: hashes.HashAlgorithm = (
            hashes.SHA256() if settings.sign_type is AlipaySignType.RSA2 else hashes.SHA1()  # noqa: S303
        )

    def sign(self, params: dict[str, str]) -> str:
        signature = self.private_key.sign(
            build_sign_string(params).encode(), padding.PKCS1v15(), self.hash
        )
        return base64.b64encode(signature).decode()

    def _verified_content(self, text: str, sign: Any) -> dict[str, Any] | None:
        """验签通过时返回被签名那段原文的解析结果。"""
        content = raw_member(text, RESPONSE_KEY)
        if content is None or not isinstance(sign, str):
            return None
        try:
            self.public_key.verify(
                base64.b64decode(sign), content.encode(), padding.PKCS1v15(), self.hash
            )
        except InvalidSignature, ValueError:
            return None
        token = json.loads(content)
        return token if isinstance(token, dict) else None

    async def exchange(self, code: str) -> ProviderIdentity:
        settings = self.settings
        params = {
            "app_id": settings.app_id,
            "method": METHOD,
            "format": "JSON",
            "charset": settings.charset,
            "sign_type": settings.sign_type.value,
            "timestamp": to_beijing_time(datetime.now(UTC)),
            "version": "1.0",
            "grant_type": "authorization_code",
            "code": code,
        }
        params["sign"] = self.sign(params)
        response = await request(
            self.transport,
            "POST",
            settings.gateway,
            label=METHOD,
            headers={"content-type": "application/x-www-form-urlencoded"},
            content=urlencode(params).encode(),
        )
        try:
            body = json.loads(response.text, object_pairs_hook=_unique_keys)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            logger.error("%s returned a non-object body", METHOD)
            raise bad_gateway()
        error = body.get("error_response")
        if isinstance(error, dict):
            sub_code = error.get("sub_code")
            logger.error("%s failed: %s %s", METHOD, error.get("code"), sub_code)
            if isinstance(sub_code, str) and sub_code.startswith("isv.code-"):
                raise code_invalid()
            raise bad_gateway()
        token = self._verified_content(response.text, body.get("sign"))
        if token is None:
            logger.error("%s response signature is missing or invalid", METHOD)
            raise bad_gateway()
        openid = token.get("open_id") or token.get("user_id")
        if not isinstance(openid, str) or not openid:
            logger.error("%s response has no open_id", METHOD)
            raise bad_gateway()
        return ProviderIdentity(openid)
