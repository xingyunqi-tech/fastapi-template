"""约定 §5.1 `GET /.well-known/jwks.json`：当前签名公钥与 `AUTH_VERIFY_KEYS`。"""

from typing import Any, Final

from fastapi.responses import JSONResponse

from app.common.context import Context
from app.common.routing import ApiRouter, Auth
from app.security.keys import jwk

JWKS_CACHE_CONTROL: Final = "max-age=300"

router = ApiRouter()


@router.endpoint(
    "GET",
    "/.well-known/jwks.json",
    operation_id="getJwks",
    auth=Auth.PUBLIC,
    envelope=False,
    include_in_schema=False,
)
async def get_jwks(context: Context) -> JSONResponse:
    keys: list[dict[str, Any]] = [jwk(key) for key in context.verifier.local_keys.values()]
    return JSONResponse({"keys": keys}, headers={"Cache-Control": JWKS_CACHE_CONTROL})
