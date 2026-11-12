import re
from typing import TYPE_CHECKING, Any

import pytest

from tests.parity.conftest import Pair
from tests.parity.test_auth_parity import normalize, tokens

if TYPE_CHECKING:
    from tests.parity.conftest import Parity

UPLOAD = "/api/v1/files/upload"
USER = "john.doe@example.com"
ADMIN = "admin@example.com"
PNG = b"\x89PNG\r\n\x1a\nabc"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_STORED_NAME = re.compile(r"/files/[^/]+\.(\w+)$")


def normalize_file(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: normalize_file(v, k) for k, v in value.items()}
    if isinstance(value, str) and key in {"id", "path"}:
        value = _UUID.sub("<uuid>", value)
        if key == "path" and "/private/" not in value:
            value = _STORED_NAME.sub(r"/files/<name>.\1", value)
    return normalize(value, key)


type Tokens = dict[str, tuple[str, str]]


@pytest.fixture(scope="module")
def session_tokens(parity: Parity) -> Tokens:
    return {email: tokens(parity, email) for email in (USER, ADMIN)}


def authed(
    parity: Parity,
    method: str,
    url: str,
    pair_tokens: tuple[str, str],
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> Pair:
    responses = [
        client.request(
            method, url, headers={**(headers or {}), "Authorization": f"Bearer {token}"}, **kwargs
        )
        for client, token in zip(parity.clients, pair_tokens, strict=True)
    ]
    return Pair(*responses)


def assert_same(pair: Pair) -> Any:
    python, nestjs = pair.both()
    assert python.status_code == nestjs.status_code, (python.text, nestjs.text)
    assert python.headers.get("content-type") == nestjs.headers.get("content-type")
    assert normalize_file(python.json()) == normalize_file(nestjs.json())
    return python.json()


def multipart(body: bytes) -> dict[str, Any]:
    return {
        "content": body,
        "headers": {"Content-Type": "multipart/form-data; boundary=xyz"},
    }


def disposition(params: str, content: bytes = b"abc") -> bytes:
    return (
        f"--xyz\r\nContent-Disposition: form-data; {params}\r\n\r\n".encode()
        + content
        + b"\r\n--xyz--\r\n"
    )


@pytest.mark.parametrize("lang", ["en", "zh-CN"])
@pytest.mark.parametrize(
    ("params", "kwargs"),
    [
        ({}, {"files": {"file": ("a.png", PNG, "image/png")}}),
        ({"purpose": "private-image"}, {"files": {"file": ("a.png", PNG, "image/png")}}),
        ({}, {"files": {"file": ("Photo.tar.JPEG", PNG, "image/jpeg")}}),
        ({}, {"files": {"file": ("../../x.png", PNG, "image/png")}}),
        ({}, {"files": {"file": ("empty.png", b"", "image/png")}}),
        ({"purpose": "xx"}, {"files": {"file": ("a.png", PNG, "image/png")}}),
        ({"purpose": ["image", "image"]}, {"files": {"file": ("a.png", PNG, "image/png")}}),
        ({"purpose": ""}, {"files": {"x": (None, "1")}}),
        ({"purpose": "audio"}, {"files": {"file": ("a.mp3", PNG, "audio/mpeg")}}),
        ({}, {"files": {"file": ("a.txt", b"hi", "text/plain")}}),
        ({}, {"files": {"file": ("png", PNG, "image/png")}}),
        ({}, {"files": {"other": ("a.png", PNG, "image/png")}}),
        ({}, {"files": [("file", ("a.png", PNG)), ("file", ("b.png", PNG))]}),
        ({}, {"files": {"file": ("", PNG, "image/png")}}),
        ({}, {"files": {"file": (None, "text")}}),
        ({}, {"json": {"fileName": "a.png", "fileSize": 10}}),
        ({}, {}),
        ({}, {"data": {"file": "abc"}}),
        ({}, {"files": {"file": ("big.png", b"\0" * (5 * 1024 * 1024 + 1), "image/png")}}),
        ({}, {"files": {"file": ("big.png", b"\0" * (5 * 1024 * 1024 + 20000), "image/png")}}),
        ({}, multipart(b"garbage")),
        ({}, multipart(b"--xyz\r\nBad Header\r\n\r\nabc\r\n--xyz--\r\n")),
        ({}, multipart(b"preamble\r\n" + disposition('name="file"; filename="a.png"'))),
        ({}, multipart(disposition('filename="a.png"'))),
        ({}, multipart(disposition('name="file"; filename="a.png"')[:-11])),
        ({}, {"content": b"x", "headers": {"Content-Type": "multipart/form-data"}}),
        ({}, {"content": b"x", "headers": {"Content-Type": "multipart/mixed; boundary=xyz"}}),
    ],
)  # fmt: skip
def test_upload(
    parity: Parity,
    session_tokens: Tokens,
    params: dict[str, Any],
    kwargs: dict[str, Any],
    lang: str,
) -> None:
    options = dict(kwargs)
    headers = {"Accept-Language": lang, **options.pop("headers", {})}
    pair_tokens = session_tokens[USER]

    assert_same(
        authed(parity, "POST", UPLOAD, pair_tokens, params=params, headers=headers, **options)
    )


def test_upload_requires_jwt(parity: Parity) -> None:
    assert_same(
        parity.request(
            "POST", UPLOAD, params={"purpose": "xx"}, files={"file": ("a.png", PNG, "image/png")}
        )
    )


def test_admin_uploads_content_asset(parity: Parity, session_tokens: Tokens) -> None:
    files = {"file": ("a.m4a", PNG, "audio/mp4")}
    params = {"purpose": "audio"}

    assert_same(authed(parity, "POST", UPLOAD, session_tokens[ADMIN], params=params, files=files))


def _upload_pair(
    parity: Parity, session_tokens: Tokens, purpose: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    pair = authed(
        parity,
        "POST",
        UPLOAD,
        session_tokens[USER],
        params={"purpose": purpose},
        files={"file": ("a.png", PNG, "image/png")},
    )
    python, nestjs = (r.json()["data"]["file"] for r in pair.both())
    return python, nestjs


def test_public_file_download(parity: Parity, session_tokens: Tokens) -> None:
    python_file, nestjs_file = _upload_pair(parity, session_tokens, "image")
    python_client, nestjs_client = parity.clients
    python = python_client.get("/api/v1/files/" + python_file["path"].rsplit("/", 1)[-1])
    nestjs = nestjs_client.get("/api/v1/files/" + nestjs_file["path"].rsplit("/", 1)[-1])

    assert (python.status_code, python.content) == (nestjs.status_code, nestjs.content)
    for header in ("content-type", "cache-control", "accept-ranges", "content-length"):
        assert python.headers.get(header) == nestjs.headers.get(header)
    assert python.headers["etag"].startswith('W/"b-')
    assert nestjs.headers["etag"].startswith('W/"b-')


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/files/nope.png",
        "/api/v1/files/upload",
        "/api/v1/files/private/abc",
        "/api/v1/files/private/00000000-0000-4000-8000-000000000000",
    ],
)
def test_download_errors(parity: Parity, session_tokens: Tokens, url: str) -> None:
    assert_same(parity.request("GET", url))
    assert_same(authed(parity, "GET", url, session_tokens[USER]))


@pytest.mark.parametrize(
    "file_id",
    [
        "abc",
        "00000000-0000-9000-0000-000000000000",
        "%7B00000000-0000-4000-8000-000000000000%7D",
        "00000000-0000-0000-0000-000000000000",
        "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
    ],
)
def test_private_file_id_validation(parity: Parity, session_tokens: Tokens, file_id: str) -> None:
    assert_same(authed(parity, "GET", f"/api/v1/files/private/{file_id}", session_tokens[USER]))


def test_private_file_access(parity: Parity, session_tokens: Tokens) -> None:
    python_file, nestjs_file = _upload_pair(parity, session_tokens, "private-image")
    python_client, nestjs_client = parity.clients
    for viewer, expected_status in ((USER, 200), (ADMIN, 403)):
        python_token, nestjs_token = session_tokens[viewer]
        python = python_client.get(
            f"/api/v1/files/private/{python_file['id']}",
            headers={"Authorization": f"Bearer {python_token}"},
        )
        nestjs = nestjs_client.get(
            f"/api/v1/files/private/{nestjs_file['id']}",
            headers={"Authorization": f"Bearer {nestjs_token}"},
        )
        assert python.status_code == nestjs.status_code == expected_status
        assert python.headers["content-type"] == nestjs.headers["content-type"]
        assert python.content == nestjs.content
