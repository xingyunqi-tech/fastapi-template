import asyncio
import logging
from typing import Any, Final

import httpx

from app.identity.providers import bad_gateway

logger = logging.getLogger("app.providers")

TIMEOUT_S: Final = 10.0


async def request(
    transport: httpx.AsyncBaseTransport | None,
    method: str,
    url: str,
    *,
    label: str,
    params: dict[str, str] | None = None,
    json: Any = None,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
) -> httpx.Response:
    """网络错误、超时或非 2xx 时 502 `common.badGateway`。"""
    try:
        async with (
            asyncio.timeout(TIMEOUT_S),
            httpx.AsyncClient(transport=transport, timeout=TIMEOUT_S) as client,
        ):
            response = await client.request(
                method, url, params=params, json=json, headers=headers, content=content
            )
    except (httpx.HTTPError, TimeoutError) as error:
        logger.error("%s request failed: %s", label, type(error).__name__)
        raise bad_gateway() from None
    if response.is_error:
        logger.error("%s HTTP %s", label, response.status_code)
        raise bad_gateway()
    return response


def json_object(response: httpx.Response, label: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        logger.error("%s returned a non-object body", label)
        raise bad_gateway()
    return body
