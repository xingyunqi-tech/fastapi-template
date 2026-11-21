import os
from contextlib import suppress
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING, Any
from uuid import UUID

import aioboto3
import anyio
import httpx2 as httpx
import pytest
from botocore.exceptions import ClientError
from sqlalchemy import select
from testcontainers.community.minio import MinioContainer

from app.infrastructure.database.session import Database
from app.modules.files import storage as files_storage
from app.modules.files import uploads
from app.modules.files.account_cleaner import FilesAccountCleaner
from app.modules.files.models import FileModel
from app.modules.files.storage import close_s3_clients
from tests.integration.conftest import _build_app, client_for
from tests.integration.test_auth import bearer, login

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, MutableMapping

    from fastapi import FastAPI
    from sqlalchemy import Row
    from sqlalchemy.ext.asyncio import AsyncConnection

    from app.common.context import AppContext

MINIO_IMAGE = "chainguard/minio:latest"
BUCKET = "files-test"
PNG = b"\x89PNG\r\n\x1a\nabc"
UPLOAD = "/api/v1/files/upload"
BAD_PURPOSE = (422, {"query.purpose": "enum"})
BAD_TYPE = (422, "file.typeNotAllowed")
NO_FILE = (422, {"file": "required"})
BAD_REQUEST = (400, "common.badRequest")
TOO_LARGE = (413, "file.tooLarge")
NOT_FOUND = (404, "common.notFound")


def outcome(response: httpx.Response) -> tuple[int, Any]:
    """校验失败返回 `(422, {field: rule})`，其余返回 `(status, errorKey)`。"""
    body = response.json()
    if body["errorKey"] == "common.validationFailed":
        return response.status_code, {e["field"]: e["rule"] for e in body["data"]["errors"]}
    return response.status_code, body["errorKey"]


class Ipv4MinioContainer(MinioContainer):
    """以 IPv4 回环地址访问容器端口。"""

    def get_container_host_ip(self) -> str:
        host = super().get_container_host_ip()
        return "127.0.0.1" if host == "localhost" else host


@pytest.fixture(scope="session")
def minio_endpoint() -> Iterator[str]:
    with Ipv4MinioContainer(MINIO_IMAGE) as container:
        container.get_client().make_bucket(BUCKET)
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9000)}"


type AppFactory = Callable[..., FastAPI]


@pytest.fixture(autouse=True)
async def release_s3_clients() -> AsyncIterator[None]:
    yield
    await close_s3_clients()


@pytest.fixture
def make_app(connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch) -> AppFactory:
    def factory(driver: str = "local", endpoint: str | None = None, **env: str) -> FastAPI:
        monkeypatch.setenv("FILE_DRIVER", driver)
        monkeypatch.setenv("BACKEND_DOMAIN", "http://backend.test")
        if driver != "local":
            assert endpoint is not None
            for name, value in {
                "ACCESS_KEY_ID": "minioadmin",
                "SECRET_ACCESS_KEY": "minioadmin",
                "AWS_DEFAULT_S3_BUCKET": BUCKET,
                "AWS_S3_REGION": "us-east-1",
                "AWS_S3_ENDPOINT": endpoint,
            }.items():
                monkeypatch.setenv(name, value)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return _build_app(Database.bound_to(connection))

    return factory


@pytest.fixture
async def local_client(make_app: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(make_app()) as client:
        yield client


async def token(client: httpx.AsyncClient, email: str = "john.doe@example.com") -> dict[str, str]:
    return bearer((await login(client, email))["token"])


def png(name: str = "a.png", content: bytes = PNG) -> dict[str, Any]:
    return {"file": (name, content, "image/png")}


async def s3_object(endpoint: str, key: str) -> bytes | None:
    async with aioboto3.Session().client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
    ) as client:
        try:
            output = await client.get_object(Bucket=BUCKET, Key=key)
        except client.exceptions.NoSuchKey:
            return None
        body: bytes = await output["Body"].read()
        return body


def read_stored(name: str) -> bytes:
    return Path("files", name).read_bytes()


def stored_files() -> list[str]:
    root = Path("files")
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


async def rows(connection: AsyncConnection) -> list[Row[tuple[UUID, str, int | None]]]:
    query = select(FileModel.id, FileModel.path, FileModel.owner_user_id.label("owner_user_id"))
    return list(await connection.execute(query))


async def bucket_keys(endpoint: str) -> set[str]:
    async with aioboto3.Session().client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
    ) as client:
        listing = await client.list_objects_v2(Bucket=BUCKET)
        return {item["Key"] for item in listing.get("Contents", [])}


class TestLocalUpload:
    async def test_upload_returns_201_and_stores_file(
        self, local_client: httpx.AsyncClient
    ) -> None:
        headers = await token(local_client)

        response = await local_client.post(UPLOAD, headers=headers, files=png("Photo.PNG"))

        assert response.status_code == 201
        body = response.json()
        file = body["data"]["file"]
        assert body["code"] == 0
        assert list(file) == ["id", "path", "visibility", "mimeType", "size"]
        [name] = stored_files()
        assert file["path"] == f"http://backend.test/api/v1/files/{name}"
        assert name.endswith(".png")
        assert (file["visibility"], file["mimeType"], file["size"]) == ("PUBLIC", "image/png", 11)
        assert read_stored(name) == PNG

    async def test_private_image_path_points_to_private_endpoint(
        self, local_client: httpx.AsyncClient
    ) -> None:
        headers = await token(local_client)

        response = await local_client.post(
            UPLOAD, params={"purpose": "private-image"}, headers=headers, files=png()
        )

        file = response.json()["data"]["file"]
        assert file["path"] == f"http://backend.test/api/v1/files/private/{file['id']}"
        assert file["visibility"] == "PRIVATE"

    @pytest.mark.parametrize(
        ("params", "files", "expected"),
        [
            ({"purpose": "xx"}, png(), BAD_PURPOSE),
            ({"purpose": ["image", "image"]}, png(), BAD_PURPOSE),
            ({"purpose": "audio"}, png(), (403, "common.forbidden")),
            ({}, {"file": ("a.txt", b"hi", "text/plain")}, BAD_TYPE),
            ({}, {"file": ("png", PNG, "image/png")}, BAD_TYPE),
            ({}, {"other": ("a.png", PNG, "image/png")}, BAD_REQUEST),
            ({}, [("file", ("a.png", PNG)), ("file", ("b.png", PNG))], BAD_REQUEST),
            ({}, {"file": ("", PNG, "image/png")}, NO_FILE),
            ({"purpose": ""}, png(), BAD_PURPOSE),
        ],
    )
    async def test_upload_errors(
        self,
        local_client: httpx.AsyncClient,
        params: dict[str, Any],
        files: Any,
        expected: tuple[int, Any],
    ) -> None:
        headers = await token(local_client)

        response = await local_client.post(UPLOAD, params=params, headers=headers, files=files)

        assert outcome(response) == expected
        assert stored_files() == []

    async def test_missing_file_in_form_or_json_body(self, local_client: httpx.AsyncClient) -> None:
        headers = await token(local_client)

        form = await local_client.post(UPLOAD, headers=headers, files={"x": (None, "1")})
        text_field = await local_client.post(UPLOAD, headers=headers, files={"file": (None, "1")})
        json_body = await local_client.post(UPLOAD, headers=headers, json={})
        empty = await local_client.post(UPLOAD, headers=headers)

        for response in (form, text_field, json_body, empty):
            assert outcome(response) == NO_FILE

    async def test_requires_jwt_before_purpose_check(self, local_client: httpx.AsyncClient) -> None:
        response = await local_client.post(UPLOAD, params={"purpose": "xx"}, files=png())

        assert outcome(response) == (401, "common.unauthorized")

    async def test_admin_may_upload_content_purpose_without_owner(
        self, local_client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        headers = await token(local_client, "admin@example.com")

        response = await local_client.post(
            UPLOAD, params={"purpose": "audio"}, headers=headers, files=png("a.mp3")
        )

        file = response.json()["data"]["file"]
        assert (response.status_code, file["mimeType"]) == (201, "audio/mpeg")
        [row] = [r for r in await rows(connection) if str(r.id) == file["id"]]
        assert row.owner_user_id is None

    @pytest.mark.parametrize("body", [b"garbage", b"--xyz\r\nBad Header\r\n\r\nx\r\n--xyz--\r\n"])
    async def test_malformed_multipart(self, local_client: httpx.AsyncClient, body: bytes) -> None:
        headers = await token(local_client)
        headers["Content-Type"] = "multipart/form-data; boundary=xyz"

        response = await local_client.post(UPLOAD, headers=headers, content=body)

        assert outcome(response) == BAD_REQUEST
        assert response.json()["data"]["reason"].startswith("Multipart: ")

    async def test_multipart_without_boundary(self, local_client: httpx.AsyncClient) -> None:
        headers = await token(local_client)
        headers["Content-Type"] = "multipart/form-data"

        response = await local_client.post(UPLOAD, headers=headers, content=b"x")

        assert outcome(response) == BAD_REQUEST
        assert response.json()["data"] == {"reason": "Multipart: Boundary not found"}


def form_body(*parts: bytes, preamble: bytes = b"") -> bytes:
    return preamble + b"".join(b"--xyz\r\n" + part + b"\r\n" for part in parts) + b"--xyz--\r\n"


def text_part(name: str, value: bytes = b"1") -> bytes:
    return f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + value


def file_part(content: bytes = PNG, name: str = "a.png") -> bytes:
    disposition = f'Content-Disposition: form-data; name="file"; filename="{name}"'
    return disposition.encode() + b"\r\nContent-Type: image/png\r\n\r\n" + content


async def one_byte_chunks(body: bytes) -> AsyncIterator[bytes]:
    for index in range(len(body)):
        yield body[index : index + 1]


async def post_disconnecting(
    app: FastAPI, headers: dict[str, str], chunks: list[bytes]
) -> list[MutableMapping[str, Any]]:
    """发送部分请求体后返回 http.disconnect。"""
    messages: list[dict[str, Any]] = [
        {"type": "http.request", "body": chunk, "more_body": True} for chunk in chunks
    ]

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    sent: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": UPLOAD,
        "raw_path": UPLOAD.encode(),
        "query_string": b"purpose=image",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"content-type", b"multipart/form-data; boundary=xyz"),
            *((name.lower().encode(), value.encode()) for name, value in headers.items()),
        ],
        "client": ("203.0.113.7", 50000),
        "server": ("test", 80),
    }
    with suppress(Exception):
        await app(scope, receive, send)
    return sent


class TestMultipartParsing:
    async def test_one_byte_chunks_with_preamble(self, local_client: httpx.AsyncClient) -> None:
        headers = await token(local_client)
        headers["Content-Type"] = "multipart/form-data; boundary=xyz"
        content = PNG + b"\r\n--xy\r\n-" + PNG
        body = form_body(
            text_part("note", b"--xyz"),
            file_part(content),
            preamble=b"ignored preamble -- xyz\r\n--xy\r\n",
        )

        response = await local_client.post(UPLOAD, headers=headers, content=one_byte_chunks(body))

        assert response.status_code == 201, response.text
        [name] = stored_files()
        assert read_stored(name) == content
        assert response.json()["data"]["file"]["size"] == len(content)

    @pytest.mark.parametrize(
        ("field", "message"),
        [
            ("a[1001]", "Field name array index too large - a[1001]"),
            ("a[b][2000][]", "Field name array index too large - a[b][2000][]"),
            ("a" + "[b]" * 33, "Field name nesting too deep - a" + "[b]" * 33),
        ],
    )
    async def test_invalid_text_field_names(
        self, local_client: httpx.AsyncClient, field: str, message: str
    ) -> None:
        headers = await token(local_client)
        headers["Content-Type"] = "multipart/form-data; boundary=xyz"

        response = await local_client.post(
            UPLOAD, headers=headers, content=form_body(file_part(), text_part(field))
        )

        assert outcome(response) == BAD_REQUEST
        assert response.json()["data"] == {"reason": message}
        assert stored_files() == []

    @pytest.mark.parametrize(
        "field", ["a[1000]", "a" + "[b]" * 32, "a[1001]x", "[1001]", "a[1001", "a[ 2000]"]
    )
    async def test_accepted_text_field_names(
        self, local_client: httpx.AsyncClient, field: str
    ) -> None:
        headers = await token(local_client)
        headers["Content-Type"] = "multipart/form-data; boundary=xyz"

        response = await local_client.post(
            UPLOAD, headers=headers, content=form_body(text_part(field), file_part())
        )

        assert response.status_code == 201, response.text

    async def test_client_disconnect_removes_local_file(self, make_app: AppFactory) -> None:
        app = make_app()
        async with client_for(app) as client:
            headers = await token(client)
        head = form_body(file_part(b"x" * 4096))[:-20]

        sent = await post_disconnecting(app, headers, [head[:100], head[100:]])

        assert not any(m.get("status") == 201 for m in sent)
        assert stored_files() == []

    async def test_client_disconnect_closes_s3_spool_without_upload(
        self, make_app: AppFactory, minio_endpoint: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spools: list[SpooledTemporaryFile[bytes]] = []

        def tracked_spool(*args: Any, **kwargs: Any) -> SpooledTemporaryFile[bytes]:
            spools.append(SpooledTemporaryFile(*args, **kwargs))  # noqa: SIM115
            return spools[-1]

        monkeypatch.setattr(uploads, "SpooledTemporaryFile", tracked_spool)
        before = await bucket_keys(minio_endpoint)
        app = make_app("s3", minio_endpoint)
        async with client_for(app) as client:
            headers = await token(client)
        head = form_body(file_part(b"x" * (2 * uploads.SPOOL_MAX_MEMORY)))[:-20]

        sent = await post_disconnecting(app, headers, [head[:100], head[100:]])

        assert not any(m.get("status") == 201 for m in sent)
        [spool] = spools
        assert getattr(spool, "_rolled") is True  # noqa: B009
        assert spool.closed
        assert await bucket_keys(minio_endpoint) == before


class TestLocalUploadSizeLimits:
    async def test_content_length_over_limit_is_rejected_before_reading(
        self, make_app: AppFactory
    ) -> None:
        async with client_for(make_app(FILE_IMAGE_MAX_SIZE="10")) as client:
            headers = await token(client)
            headers["Accept-Language"] = "zh"
            big = png(content=b"x" * (20 * 1024))

            response = await client.post(UPLOAD, headers=headers, files=big)

        assert outcome(response) == TOO_LARGE
        assert response.json()["message"] == "文件过大"
        assert stored_files() == []

    async def test_file_over_purpose_limit_is_removed(self, make_app: AppFactory) -> None:
        async with client_for(make_app(FILE_IMAGE_MAX_SIZE="10")) as client:
            headers = await token(client)

            response = await client.post(UPLOAD, headers=headers, files=png())

        assert outcome(response) == TOO_LARGE
        assert stored_files() == []

    async def test_stream_over_max_file_size_is_aborted(self, make_app: AppFactory) -> None:
        sizes = {
            f"FILE_{name}_MAX_SIZE": "10"
            for name in ("IMAGE", "PRIVATE_IMAGE", "CONTENT_IMAGE", "AUDIO", "IMPORT", "EXPORT")
        }
        async with client_for(make_app(**sizes)) as client:
            headers = await token(client)
            headers["Content-Type"] = "multipart/form-data; boundary=xyz"
            body = (
                b'--xyz\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\n\r\n'
                + b"x" * 100
                + b"\r\n--xyz--\r\n"
            )

            async def chunks() -> AsyncIterator[bytes]:
                yield body

            response = await client.post(UPLOAD, headers=headers, content=chunks())

        assert outcome(response) == TOO_LARGE
        assert stored_files() == []


async def upload(client: httpx.AsyncClient, headers: dict[str, str], purpose: str) -> Any:
    response = await client.post(UPLOAD, params={"purpose": purpose}, headers=headers, files=png())
    assert response.status_code == 201, response.text
    return response.json()["data"]["file"]


class TestLocalDownload:
    async def test_public_file_is_served_with_express_headers(
        self, local_client: httpx.AsyncClient
    ) -> None:
        file = await upload(local_client, await token(local_client), "image")
        url = file["path"].removeprefix("http://backend.test")

        response = await local_client.get(url)

        assert response.status_code == 200
        assert response.content == PNG
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "public, max-age=0"
        assert response.headers["accept-ranges"] == "bytes"
        assert response.headers["etag"].startswith('W/"b-')
        assert "last-modified" in response.headers
        cached = await local_client.get(url, headers={"If-None-Match": response.headers["etag"]})
        assert cached.status_code == 304

    @pytest.mark.parametrize(
        ("value", "status", "content", "content_range"),
        [
            ("bytes=abc", 200, PNG, None),
            ("items=0-1", 200, PNG, None),
            ("bytes=", 200, PNG, None),
            ("bytes=0-1,3-4", 200, PNG, None),
            ("bytes=0-1", 206, PNG[:2], "bytes 0-1/11"),
            ("bytes=-3", 206, PNG[-3:], "bytes 8-10/11"),
            ("bytes=3-", 206, PNG[3:], "bytes 3-10/11"),
            ("bytes=5-100", 206, PNG[5:], "bytes 5-10/11"),
            ("bytes=0-1,1-2", 206, PNG[:3], "bytes 0-2/11"),
        ],
    )
    async def test_range_follows_express_send(
        self,
        local_client: httpx.AsyncClient,
        value: str,
        status: int,
        content: bytes,
        content_range: str | None,
    ) -> None:
        file = await upload(local_client, await token(local_client), "image")
        url = file["path"].removeprefix("http://backend.test")

        response = await local_client.get(url, headers={"Range": value})

        assert response.status_code == status
        assert response.content == content
        assert response.headers["content-length"] == str(len(content))
        assert response.headers.get("content-range") == content_range

    @pytest.mark.parametrize("value", ["bytes=5-2", "bytes=100-200", "bytes=-0"])
    async def test_unsatisfiable_range_is_416(
        self, local_client: httpx.AsyncClient, value: str
    ) -> None:
        file = await upload(local_client, await token(local_client), "image")
        url = file["path"].removeprefix("http://backend.test")

        response = await local_client.get(url, headers={"Range": value})

        assert response.status_code == 416
        assert response.text == "Range Not Satisfiable"
        assert response.headers["content-range"] == "bytes */11"
        assert response.headers["accept-ranges"] == "bytes"

    async def test_conditional_requests_follow_express_send(
        self, local_client: httpx.AsyncClient
    ) -> None:
        file = await upload(local_client, await token(local_client), "image")
        url = file["path"].removeprefix("http://backend.test")
        first = await local_client.get(url)
        etag, modified = first.headers["etag"], first.headers["last-modified"]
        epoch = "Thu, 01 Jan 1970 00:00:00 GMT"

        async def status(headers: dict[str, str]) -> int:
            return (await local_client.get(url, headers=headers)).status_code

        assert await status({"If-None-Match": "*"}) == 304
        assert await status({"If-None-Match": 'W/"x"'}) == 200
        assert await status({"If-None-Match": etag, "Cache-Control": "no-cache"}) == 200
        assert await status({"If-Modified-Since": modified}) == 304
        assert await status({"If-Modified-Since": epoch}) == 200
        assert await status({"If-Match": "*"}) == 200
        assert await status({"If-Range": etag, "Range": "bytes=0-1"}) == 206
        assert await status({"If-Range": modified, "Range": "bytes=0-1"}) == 206
        assert await status({"If-Range": '"x"', "Range": "bytes=0-1"}) == 200
        assert await status({"If-Range": '"x"', "Range": "bytes=100-200"}) == 200
        for headers in ({"If-Match": '"x"'}, {"If-Unmodified-Since": epoch}):
            failed = await local_client.get(url, headers=headers)
            assert failed.status_code == 412
            assert failed.text == "Precondition Failed"
        duplicated = [
            [("Range", "bytes=0-1"), ("Range", "bytes=0-1")],
            [("If-None-Match", 'W/"x"'), ("If-None-Match", etag)],
            [("If-Modified-Since", epoch), ("If-Modified-Since", modified)],
        ]
        statuses = [
            (await local_client.get(url, headers=headers)).status_code for headers in duplicated
        ]
        assert statuses == [200, 304, 200]
        head = await local_client.head(url, headers={"Range": "bytes=0-1"})
        assert (head.status_code, head.headers["content-length"]) == (206, "2")
        assert head.headers["content-range"] == "bytes 0-1/11"

    async def test_unknown_or_private_file_is_not_found(
        self, local_client: httpx.AsyncClient
    ) -> None:
        private = await upload(local_client, await token(local_client), "private-image")
        [name] = stored_files()

        missing = await local_client.get("/api/v1/files/nope.png")
        hidden = await local_client.get(f"/api/v1/files/{name}")
        upload_get = await local_client.get(UPLOAD)

        assert private["visibility"] == "PRIVATE"
        for response in (missing, hidden, upload_get):
            assert outcome(response) == NOT_FOUND

    async def test_private_file_access_rules(self, local_client: httpx.AsyncClient) -> None:
        owner = await token(local_client)
        other = await token(local_client, "admin@example.com")
        file = await upload(local_client, owner, "private-image")
        url = f"/api/v1/files/private/{file['id']}"

        allowed = await local_client.get(url, headers=owner)
        denied = await local_client.get(url, headers=other)
        anonymous = await local_client.get(url)

        assert (allowed.status_code, allowed.content) == (200, PNG)
        assert outcome(denied) == (403, "common.forbidden")
        assert outcome(anonymous) == (401, "common.unauthorized")

    @pytest.mark.parametrize(
        ("file_id", "expected"),
        [
            ("abc", (422, {"path.id": "uuid"})),
            ("00000000-0000-9000-0000-000000000000", NOT_FOUND),
            ("00000000-0000-0000-0000-000000000000", NOT_FOUND),
            ("AAAAAAAA-AAAA-1AAA-8AAA-AAAAAAAAAAAA", NOT_FOUND),
        ],
    )
    async def test_private_file_id_validation(
        self, local_client: httpx.AsyncClient, file_id: str, expected: tuple[int, Any]
    ) -> None:
        headers = await token(local_client)

        response = await local_client.get(f"/api/v1/files/private/{file_id}", headers=headers)

        assert outcome(response) == expected

    async def test_public_file_through_private_endpoint(
        self, local_client: httpx.AsyncClient
    ) -> None:
        file = await upload(local_client, await token(local_client), "image")
        admin = await token(local_client, "admin@example.com")

        response = await local_client.get(f"/api/v1/files/private/{file['id']}", headers=admin)

        assert (response.status_code, response.content) == (200, PNG)


class TestS3Upload:
    async def test_upload_streams_to_bucket_and_returns_signed_url(
        self, make_app: AppFactory, minio_endpoint: str
    ) -> None:
        async with client_for(make_app("s3", minio_endpoint)) as client:
            response = await client.post(UPLOAD, headers=await token(client), files=png())

        file = response.json()["data"]["file"]
        key = file["path"].split("?")[0].rsplit("/", 1)[-1]
        assert response.status_code == 201
        assert file["path"].startswith(f"{minio_endpoint}/{BUCKET}/{key}?")
        assert "X-Amz-Signature=" in file["path"]
        assert await s3_object(minio_endpoint, key) == PNG
        async with httpx.AsyncClient(trust_env=False) as plain:
            assert (await plain.get(file["path"])).content == PNG

    async def test_client_is_created_once_and_released_on_close(
        self, make_app: AppFactory, minio_endpoint: str
    ) -> None:
        sessions: list[aioboto3.Session] = []
        real_session = aioboto3.Session

        def counting_session() -> aioboto3.Session:
            sessions.append(real_session())
            return sessions[-1]

        app = make_app("s3", minio_endpoint)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(aioboto3, "Session", counting_session)
            async with client_for(app) as client:
                headers = await token(client)
                files = [await upload(client, headers, "image") for _ in range(3)]

        assert len(sessions) == 1
        assert len(files_storage._S3_CLIENTS._clients) == 1
        await close_s3_clients()
        assert files_storage._S3_CLIENTS._clients == {}
        key = files[0]["path"].split("?")[0].rsplit("/", 1)[-1]
        storage = files_storage.FileStorage(app.state.settings.file)
        assert await storage.read(key) == PNG

    async def test_oversized_upload_is_deleted_from_bucket(
        self, make_app: AppFactory, minio_endpoint: str
    ) -> None:
        before = await bucket_keys(minio_endpoint)
        async with client_for(make_app("s3", minio_endpoint, FILE_IMAGE_MAX_SIZE="10")) as client:
            response = await client.post(UPLOAD, headers=await token(client), files=png())

        assert outcome(response) == TOO_LARGE
        assert await bucket_keys(minio_endpoint) == before

    async def test_private_file_redirects_to_signed_url(
        self, make_app: AppFactory, minio_endpoint: str
    ) -> None:
        async with client_for(make_app("s3", minio_endpoint)) as client:
            headers = await token(client)
            file = await upload(client, headers, "private-image")
            response = await client.get(f"/api/v1/files/private/{file['id']}", headers=headers)
            not_routed = await client.get("/api/v1/files/whatever.png")

        assert response.status_code == 302
        assert response.headers["location"].startswith(f"{minio_endpoint}/{BUCKET}/")
        assert response.text.startswith("Found. Redirecting to ")
        assert outcome(not_routed) == NOT_FOUND


class TestS3PresignedUpload:
    async def test_returns_file_and_upload_signed_url(
        self, make_app: AppFactory, minio_endpoint: str
    ) -> None:
        async with client_for(make_app("s3-presigned", minio_endpoint)) as client:
            response = await client.post(
                UPLOAD,
                headers=await token(client),
                json={"fileName": "Photo.JPG", "fileSize": len(PNG)},
            )

        data = response.json()["data"]
        assert response.status_code == 201
        assert list(data) == ["file", "uploadSignedUrl"]
        assert (data["file"]["mimeType"], data["file"]["size"]) == ("image/jpeg", len(PNG))
        async with httpx.AsyncClient(trust_env=False) as plain:
            put = await plain.put(data["uploadSignedUrl"], content=PNG)
            assert put.status_code == 200, put.text
            assert (await plain.get(data["file"]["path"])).content == PNG

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ({"fileName": "a.txt", "fileSize": 1}, BAD_TYPE),
            ({"fileName": "a.png", "fileSize": 6 * 1024 * 1024}, TOO_LARGE),
            ({}, (422, {"fileName": "required", "fileSize": "required"})),
        ],
    )
    async def test_errors(
        self,
        make_app: AppFactory,
        minio_endpoint: str,
        body: dict[str, Any],
        expected: tuple[int, Any],
    ) -> None:
        async with client_for(make_app("s3-presigned", minio_endpoint)) as client:
            response = await client.post(UPLOAD, headers=await token(client), json=body)

        assert outcome(response) == expected


async def _cleaner_scenario(
    client: httpx.AsyncClient,
    app: FastAPI,
    connection: AsyncConnection,
    exists: Callable[[str], Awaitable[bool]],
) -> None:
    headers = await token(client)
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]
    kept = await upload(client, headers, "image")
    removed = [await upload(client, headers, purpose) for purpose in ("image", "private-image")]
    content = await upload(client, await token(client, "admin@example.com"), "content-image")
    await client.put("/api/v1/auth/me", headers=headers, json={"photo": {"id": kept["id"]}})
    paths = {r.id: r.path for r in await rows(connection)}

    context: AppContext = app.state.context
    await FilesAccountCleaner(context.database, context.settings.file).clean(me["id"])

    remaining = {r.id for r in await rows(connection)}
    assert UUID(kept["id"]) in remaining
    assert UUID(content["id"]) in remaining
    for file in removed:
        assert UUID(file["id"]) not in remaining
        assert not await exists(paths[UUID(file["id"])])
    assert await exists(paths[UUID(kept["id"])])


class TestFilesAccountCleaner:
    async def test_local_removes_owned_files_and_keeps_referenced(
        self, make_app: AppFactory, connection: AsyncConnection
    ) -> None:
        app = make_app()

        async def exists(path: str) -> bool:
            return await anyio.Path("files", os.path.basename(path)).exists()

        async with client_for(app) as client:
            await _cleaner_scenario(client, app, connection, exists)

    async def test_s3_removes_objects(
        self, make_app: AppFactory, minio_endpoint: str, connection: AsyncConnection
    ) -> None:
        app = make_app("s3", minio_endpoint)

        async def exists(key: str) -> bool:
            return await s3_object(minio_endpoint, key) is not None

        async with client_for(app) as client:
            await _cleaner_scenario(client, app, connection, exists)

    async def test_storage_failure_keeps_earlier_deletions(
        self,
        make_app: AppFactory,
        minio_endpoint: str,
        connection: AsyncConnection,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        app = make_app("s3", minio_endpoint)
        async with client_for(app) as client:
            headers = await token(client)
            me = (await client.get("/api/v1/auth/me", headers=headers)).json()["data"]
            uploaded = [await upload(client, headers, "image") for _ in range(3)]
        ids = sorted(UUID(file["id"]) for file in uploaded)
        paths = {r.id: r.path for r in await rows(connection)}
        context: AppContext = app.state.context
        cleaner = FilesAccountCleaner(context.database, context.settings.file)
        real_delete = cleaner.storage.delete
        calls: list[str] = []

        async def flaky_delete(path: str) -> None:
            calls.append(path)
            if len(calls) == 2:
                raise ClientError({"Error": {"Code": "InternalError"}}, "DeleteObject")
            await real_delete(path)

        monkeypatch.setattr(cleaner.storage, "delete", flaky_delete)

        with pytest.raises(ClientError):
            await cleaner.clean(me["id"])

        remaining = {r.id for r in await rows(connection)}
        assert calls == [paths[ids[0]], paths[ids[1]]]
        assert ids[0] not in remaining
        assert {ids[1], ids[2]} <= remaining
        assert await s3_object(minio_endpoint, paths[ids[0]]) is None
        assert await s3_object(minio_endpoint, paths[ids[1]]) == PNG
