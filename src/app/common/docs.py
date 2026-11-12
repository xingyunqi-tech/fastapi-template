lazy from pathlib import Path

from starlette.responses import HTMLResponse, Response
from starlette.routing import Route
lazy from fastapi import FastAPI
lazy from starlette.requests import Request

SCALAR_VERSION = "1.76.0"
DOCS_CSP = (
    "default-src 'self';script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net;"
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net;img-src 'self' data: https:;"
    "font-src 'self' https: data:;connect-src 'self';object-src 'none';frame-ancestors 'self'"
)

_SCALAR_HTML = """<!doctype html>
<html>
  <head>
    <title>API</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
  </head>
  <body>
    <script id="api-reference" data-url="/docs-json"></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference@{version}"></script>
  </body>
</html>
"""


def mount_docs(app: FastAPI, baseline_path: Path) -> None:
    """`/docs-json` 逐字节输出 nestjs 基线，`/docs` 为指向它的 Scalar 页面。"""
    baseline = baseline_path.read_bytes()
    html = _SCALAR_HTML.format(version=SCALAR_VERSION)

    async def docs_json(_: Request) -> Response:
        return Response(baseline, media_type="application/json; charset=utf-8")

    async def docs_ui(_: Request) -> Response:
        return HTMLResponse(html, headers={"Content-Security-Policy": DOCS_CSP})

    app.router.routes.extend(
        [
            Route("/docs-json", docs_json, methods=["GET"], include_in_schema=False),
            Route("/docs", docs_ui, methods=["GET"], include_in_schema=False),
        ]
    )
