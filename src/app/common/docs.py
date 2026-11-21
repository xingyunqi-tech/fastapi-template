"""`DOCS_ENABLED` 时挂载 `/openapi.json` 与 Scalar 页面 `/docs`。"""

from typing import Final

from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
lazy from fastapi import FastAPI
lazy from starlette.requests import Request

SCALAR_VERSION: Final = "1.76.0"
DOCS_CSP: Final = (
    "default-src 'self';script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net;"
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net;img-src 'self' data: https:;"
    "font-src 'self' https: data:;connect-src 'self';object-src 'none';frame-ancestors 'self'"
)

_SCALAR_HTML: Final = """<!doctype html>
<html>
  <head>
    <title>{title}</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
  </head>
  <body>
    <script id="api-reference" data-url="/openapi.json"></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@{version}"></script>
  </body>
</html>
"""


def mount_docs(app: FastAPI) -> None:
    html = _SCALAR_HTML.format(title=app.title, version=SCALAR_VERSION)

    async def openapi_json(_: Request) -> Response:
        return JSONResponse(app.openapi())

    async def docs_ui(_: Request) -> Response:
        return HTMLResponse(html, headers={"Content-Security-Policy": DOCS_CSP})

    app.router.routes.extend(
        [
            Route("/openapi.json", openapi_json, methods=["GET"], include_in_schema=False),
            Route("/docs", docs_ui, methods=["GET"], include_in_schema=False),
        ]
    )
