"""阿里云 Dysmsapi SendSms（2017-05-25，RPC 风格，ACS3-HMAC-SHA256 签名）。"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Final
from urllib.parse import quote

lazy import httpx

from app.identity.providers import bad_gateway
from app.identity.providers.http import json_object, request
from app.identity.providers.sms.masking import mask_phone
lazy from app.config import SmsSettings

logger = logging.getLogger("app.sms")

API_VERSION: Final = "2017-05-25"
ACTION: Final = "SendSms"
ALGORITHM: Final = "ACS3-HMAC-SHA256"
FORM_CONTENT_TYPE: Final = "application/x-www-form-urlencoded"


def _encode(value: str) -> str:
    return quote(value, safe="-_.~")


def form_body(params: dict[str, str]) -> bytes:
    return "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(params.items())).encode()


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
    body: bytes = b"",
) -> dict[str, str]:
    """生成带 ACS3-HMAC-SHA256 签名的请求头。"""
    headers = {
        "host": host,
        "x-acs-action": action,
        "x-acs-content-sha256": _sha256_hex(body),
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


def aliyun_phone(phone: str) -> str:
    """E.164 转阿里云格式：中国大陆号码去掉 `+86`，其余去掉 `+`。"""
    return phone[3:] if phone.startswith("+86") else phone.removeprefix("+")


class AliyunSmsSender:
    def __init__(
        self, settings: SmsSettings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def send(self, phone: str, code: str) -> None:
        """参数放在表单 body 中；调用失败或返回码不是 `OK` 时 502 `common.badGateway`。"""
        settings = self.settings
        form = {
            "PhoneNumbers": aliyun_phone(phone),
            "SignName": settings.aliyun_sign_name,
            "TemplateCode": settings.aliyun_template_code,
            "TemplateParam": json.dumps({"code": code}, separators=(",", ":")),
        }
        host = settings.aliyun_endpoint
        body = form_body(form)
        headers = sign_request(
            method="POST",
            host=host,
            action=ACTION,
            version=API_VERSION,
            query={},
            body=body,
            access_key_id=settings.aliyun_access_key_id,
            access_key_secret=settings.aliyun_access_key_secret.get_secret_value(),
            date=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            nonce=uuid.uuid4().hex,
        )
        response = await request(
            self.transport,
            "POST",
            f"https://{host}/",
            label="aliyun SendSms",
            headers={**headers, "content-type": FORM_CONTENT_TYPE},
            content=body,
        )
        result = json_object(response, "aliyun SendSms")
        if result.get("Code") != "OK":
            logger.error("aliyun SendSms failed: %s %s", result.get("Code"), result.get("Message"))
            raise bad_gateway()
        logger.info("SMS sent to %s, BizId %s", mask_phone(phone), result.get("BizId"))
