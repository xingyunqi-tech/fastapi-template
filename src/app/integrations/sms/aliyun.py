"""阿里云 Dysmsapi SendSms（2017-05-25，RPC 风格，ACS3-HMAC-SHA256 签名）。"""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import quote

import httpx

from app.integrations.sms.masking import mask_phone
lazy from app.config import SmsSettings

logger = logging.getLogger("app.sms")

API_VERSION: Final = "2017-05-25"
ACTION: Final = "SendSms"
ALGORITHM: Final = "ACS3-HMAC-SHA256"
TIMEOUT_S: Final = 10.0


@dataclass(frozen=True, slots=True)
class SendSmsResult:
    success: bool
    code: str | None = None
    message: str | None = None
    request_id: str | None = None
    biz_id: str | None = None


def _encode(value: str) -> str:
    return quote(value, safe="-_.~")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign_request(
    *,
    method: str,
    host: str,
    action: str,
    version: str,
    query: dict[str, str],
    access_key_id: str,
    access_key_secret: str,
    date: str,
    nonce: str,
) -> dict[str, str]:
    """生成带 ACS3-HMAC-SHA256 签名的请求头（请求体为空）。"""
    headers = {
        "host": host,
        "x-acs-action": action,
        "x-acs-content-sha256": _sha256_hex(b""),
        "x-acs-date": date,
        "x-acs-signature-nonce": nonce,
        "x-acs-version": version,
    }
    canonical_query = "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(query.items()))
    signed = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in signed)
    signed_headers = ";".join(signed)
    canonical_request = "\n".join(
        [
            method,
            "/",
            canonical_query,
            canonical_headers,
            signed_headers,
            headers["x-acs-content-sha256"],
        ]
    )
    string_to_sign = f"{ALGORITHM}\n{_sha256_hex(canonical_request.encode())}"
    signature = hmac.new(
        access_key_secret.encode(), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return {
        **headers,
        "accept": "application/json",
        "authorization": (
            f"{ALGORITHM} Credential={access_key_id},"
            f"SignedHeaders={signed_headers},Signature={signature}"
        ),
    }


class AliyunSmsProvider:
    def __init__(
        self, settings: SmsSettings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport
        secret = settings.access_key_secret.get_secret_value()
        self.enabled = not settings.mock_mode and bool(settings.access_key_id and secret)

    async def send_sms(self, phone: str, template_param: dict[str, str]) -> SendSmsResult:
        settings = self.settings
        params = json.dumps(template_param, separators=(",", ":"), ensure_ascii=False)
        if settings.mock_mode:
            logger.info("[MOCK SMS] To: %s, Params: %s", mask_phone(phone), params)
            return SendSmsResult(
                success=True,
                code="OK",
                message="Mock SMS sent successfully",
                request_id="mock-request-id",
                biz_id="mock-biz-id",
            )
        if not self.enabled:
            logger.error("SMS client not initialized")
            return SendSmsResult(
                success=False, code="CLIENT_NOT_INITIALIZED", message="SMS client not initialized"
            )
        if not settings.sign_name or not settings.template_code:
            logger.error("SMS sign name or template code not configured")
            return SendSmsResult(
                success=False, code="CONFIG_ERROR", message="SMS configuration incomplete"
            )
        try:
            body = await self._call(
                {
                    "PhoneNumbers": phone,
                    "SignName": settings.sign_name,
                    "TemplateCode": settings.template_code,
                    "TemplateParam": params,
                }
            )
        except Exception as error:
            logger.error("SMS send error: %s", error)
            return SendSmsResult(success=False, code="SEND_ERROR", message=str(error))
        if body.get("Code") == "OK":
            logger.info(
                "SMS sent successfully to %s, BizId: %s", mask_phone(phone), body.get("BizId")
            )
            return SendSmsResult(
                success=True,
                code="OK",
                message=body.get("Message"),
                request_id=body.get("RequestId"),
                biz_id=body.get("BizId"),
            )
        logger.error("SMS send failed: %s - %s", body.get("Code"), body.get("Message"))
        return SendSmsResult(
            success=False,
            code=body.get("Code"),
            message=body.get("Message"),
            request_id=body.get("RequestId"),
        )

    async def _call(self, query: dict[str, str]) -> dict[str, Any]:
        host = self.settings.endpoint
        headers = sign_request(
            method="POST",
            host=host,
            action=ACTION,
            version=API_VERSION,
            query=query,
            access_key_id=self.settings.access_key_id,
            access_key_secret=self.settings.access_key_secret.get_secret_value(),
            date=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            nonce=uuid.uuid4().hex,
        )
        canonical_query = "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(query.items()))
        async with (
            asyncio.timeout(TIMEOUT_S),
            httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT_S) as client,
        ):
            response = await client.post(f"https://{host}/?{canonical_query}", headers=headers)
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("SMS response body is empty")
        if response.is_error:
            raise RuntimeError(f"{body.get('Code')}: {body.get('Message')}")
        return body
