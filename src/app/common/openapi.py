"""code-first OpenAPI 3.1：`/openapi.json` 与 `app openapi export` 共用。"""

import json
from importlib import metadata
from pathlib import Path
from typing import Any, Final

from fastapi.openapi.utils import get_openapi
lazy from fastapi import FastAPI

OPENAPI_PATH: Final = Path(__file__).resolve().parents[3] / "openapi" / "openapi.json"
SECURITY_SCHEMES: Final = {
    "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
}


def package_version() -> str:
    try:
        return metadata.version("fastapi-temp")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def build_openapi(app: FastAPI) -> dict[str, Any]:
    schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version="3.1.0",
        routes=app.routes,
        separate_input_output_schemas=False,
    )
    schema.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
    return schema


def install_openapi(app: FastAPI) -> None:
    cached: dict[str, Any] = {}

    def openapi() -> dict[str, Any]:
        if not cached:
            cached.update(build_openapi(app))
        return cached

    app.openapi = openapi  # type: ignore[method-assign]


def render_openapi(app: FastAPI) -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
