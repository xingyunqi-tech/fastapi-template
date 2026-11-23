import asyncio
import io
import os
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import httpx as httpx_lib
import pytest
from sqlalchemy import text

from app.infrastructure.database.session import Database
from app.modules.files.account_cleaner import FilesAccountCleaner
from app.modules.files.service import FilesService
from app.modules.files.storage import close_s3_clients
from tests.integration.conftest import Minio, _build_app, client_for, error_of, rules_of
from tests.integration.identity import auth_headers, bearer, login

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    import httpx2 as httpx
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncConnection

UPLOAD = "/api/v1/files"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64
MIB = 1024 * 1024
PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

type AppFactory = Callable[..., FastAPI]


@pytest.fixture
def make_app(
    connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AppFactory:
    def factory(**env: str) -> FastAPI:
        monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path))
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return _build_app(Database.bound_to(connection))

    return factory


@pytest.fixture
async def client(make_app: AppFactory) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(make_app()) as http:
        yield http


async def upload(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    body: bytes = PNG,
    purpose: str | None = "avatar",
    name: str = "a.png",
) -> httpx.Response:
    data = {} if purpose is None else {"purpose": purpose}
    return await client.post(
        UPLOAD, files={"file": (name, body, "application/octet-stream")}, data=data, headers=headers
    )


def content_path(url: str) -> str:
    assert url.startswith("/api/v1/files/")
    return url


class TestUpload:
    async def test_avatar_is_stored_and_served_by_signed_url(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        headers = await auth_headers(client)
        response = await upload(client, headers)

        assert response.status_code == 201, response.text
        file = response.json()["data"]
        assert set(file) == {"id", "name", "size", "contentType", "purpose", "url", "createdAt"}
        assert (file["name"], file["size"], file["contentType"], file["purpose"]) == (
            "a.png",
            len(PNG),
            "image/png",
            "avatar",
        )
        [stored] = os.listdir(tmp_path)
        assert await asyncio.to_thread((tmp_path / stored).read_bytes) == PNG

        content = await client.get(content_path(file["url"]))
        assert content.status_code == 200
        assert content.content == PNG
        assert content.headers["content-type"] == "image/png"
        assert content.headers["x-content-type-options"] == "nosniff"
        assert content.headers["cache-control"] == "private, no-store"
        assert "content-disposition" not in content.headers

    async def test_attachment_is_downloaded_as_attachment(self, client: httpx.AsyncClient) -> None:
        headers = await auth_headers(client)
        response = await upload(client, headers, PDF, "attachment", "报告 1.pdf")
        file = response.json()["data"]

        content = await client.get(content_path(file["url"]))
        assert content.headers["content-type"] == "application/pdf"
        assert content.headers["content-disposition"] == (
            "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A%201.pdf"
        )

    @pytest.mark.parametrize(
        ("body", "purpose"),
        [
            (PDF, "avatar"),
            (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "avatar"),
            (b"plain text", "attachment"),
        ],
    )
    async def test_type_is_sniffed_from_content(
        self, client: httpx.AsyncClient, body: bytes, purpose: str
    ) -> None:
        response = await upload(client, await auth_headers(client), body, purpose, "a.png")
        assert error_of(response) == (422, "file.typeNotAllowed")

    async def test_size_limit_follows_purpose(self, client: httpx.AsyncClient) -> None:
        headers = await auth_headers(client)
        big = PNG + b"\x00" * (5 * MIB)

        assert error_of(await upload(client, headers, big)) == (413, "file.tooLarge")
        assert (await upload(client, headers, big, "attachment")).status_code == 201
        huge = PDF + b"\x00" * (20 * MIB)
        assert error_of(await upload(client, headers, huge, "attachment")) == (
            413,
            "file.tooLarge",
        )

    async def test_form_validation(self, client: httpx.AsyncClient) -> None:
        headers = await auth_headers(client)

        assert rules_of(await upload(client, headers, purpose=None)) == {"purpose": "required"}
        assert rules_of(await upload(client, headers, purpose="banner")) == {"purpose": "enum"}
        missing = await client.post(UPLOAD, data={"purpose": "avatar"}, headers=headers)
        assert rules_of(missing) == {"file": "required"}

    async def test_requires_jwt_and_permission(
        self, client: httpx.AsyncClient, connection: AsyncConnection
    ) -> None:
        anonymous = await upload(client, {})
        await connection.execute(
            text(
                "DELETE FROM user_roles WHERE user_id = "
                "(SELECT id FROM users WHERE email = 'john.doe@example.com')"
            )
        )
        tokens = await login(client, "john.doe@example.com")
        forbidden = await upload(client, bearer(tokens["accessToken"]))

        assert error_of(anonymous) == (401, "common.unauthorized")
        assert error_of(forbidden) == (403, "common.forbidden")


class TestRead:
    async def test_get_file_is_owner_only(self, client: httpx.AsyncClient) -> None:
        owner = await auth_headers(client)
        file = (await upload(client, owner)).json()["data"]
        admin = await auth_headers(client, "admin@example.com")

        mine = await client.get(f"{UPLOAD}/{file['id']}", headers=owner)
        assert mine.json()["data"]["id"] == file["id"]
        assert error_of(await client.get(f"{UPLOAD}/{file['id']}", headers=admin)) == (
            404,
            "common.notFound",
        )
        assert error_of(await client.get(f"{UPLOAD}/{uuid.uuid4()}", headers=owner)) == (
            404,
            "common.notFound",
        )

    async def test_content_requires_valid_signature(self, client: httpx.AsyncClient) -> None:
        file = (await upload(client, await auth_headers(client))).json()["data"]
        url = urlsplit(file["url"])
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        base = url.path

        for params in (
            {},
            {"expires": query["expires"]},
            {"expires": query["expires"], "signature": "x" * 43},
            {"expires": str(int(query["expires"]) + 1), "signature": query["signature"]},
            {"expires": "1", "signature": query["signature"]},
            {"expires": "abc", "signature": query["signature"]},
            {"expires": "²", "signature": query["signature"]},
            {"expires": "9" * 5000, "signature": query["signature"]},
            {"expires": query["expires"], "signature": "é" * 64},
        ):
            response = await client.get(base, params=params)
            assert error_of(response) == (404, "common.notFound"), params


class TestRollback:
    async def test_stored_object_is_removed_when_transaction_fails(
        self, make_app: AppFactory, tmp_path: Path
    ) -> None:
        context = make_app().state.context
        stored: list[str] = []

        async def failing_upload() -> None:
            async with context.database.transaction() as session:
                await FilesService(session).upload(
                    context.settings.storage,
                    owner_id="u",
                    purpose_value="avatar",
                    name="a.png",
                    body=io.BytesIO(PNG),
                )
                stored.extend(os.listdir(tmp_path))
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            await failing_upload()

        assert len(stored) == 1
        assert os.listdir(tmp_path) == []


class TestAccountCleanup:
    async def test_cleaner_removes_objects_and_rows(
        self, make_app: AppFactory, connection: AsyncConnection, tmp_path: Path
    ) -> None:
        app = make_app()
        async with client_for(app) as client:
            tokens = await login(client, "john.doe@example.com")
            for _ in range(2):
                await upload(client, bearer(tokens["accessToken"]))
        context = app.state.context

        cleaner = FilesAccountCleaner(context.database, context.settings.storage)
        await cleaner.clean(uuid.UUID(tokens["user"]["id"]))
        await cleaner.clean(uuid.UUID(tokens["user"]["id"]))

        assert os.listdir(tmp_path) == []
        assert await connection.scalar(text("SELECT count(*) FROM files")) == 0


@pytest.fixture
async def s3_app(
    make_app: AppFactory, minio_server: Minio, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[FastAPI, Any]]:
    import boto3

    for name in PROXY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    bucket = f"files-{uuid.uuid4().hex[:8]}"
    s3 = boto3.client(
        "s3",
        endpoint_url=minio_server.endpoint,
        aws_access_key_id=minio_server.access_key,
        aws_secret_access_key=minio_server.secret_key,
        region_name="us-east-1",
    )
    s3.create_bucket(Bucket=bucket)
    app = make_app(
        STORAGE_DRIVER="s3",
        S3_BUCKET=bucket,
        S3_REGION="us-east-1",
        S3_ENDPOINT=minio_server.endpoint,
        S3_FORCE_PATH_STYLE="true",
        S3_ACCESS_KEY_ID=minio_server.access_key,
        S3_SECRET_ACCESS_KEY=minio_server.secret_key,
    )
    yield app, (s3, bucket)
    await close_s3_clients()


class TestS3:
    async def test_upload_presigned_url_and_cleanup(self, s3_app: tuple[FastAPI, Any]) -> None:
        app, (s3, bucket) = s3_app
        async with client_for(app) as client:
            tokens = await login(client, "john.doe@example.com")
            response = await upload(client, bearer(tokens["accessToken"]), PDF, "attachment")

        assert response.status_code == 201, response.text
        file = response.json()["data"]
        [item] = s3.list_objects_v2(Bucket=bucket)["Contents"]
        assert s3.head_object(Bucket=bucket, Key=item["Key"])["ContentType"] == "application/pdf"

        query = parse_qs(urlsplit(file["url"]).query)
        assert query["X-Amz-Expires"] == ["600"]
        async with httpx_lib.AsyncClient(trust_env=False) as http:
            downloaded = await http.get(file["url"])
        assert downloaded.status_code == 200
        assert downloaded.content == PDF
        assert downloaded.headers["content-disposition"].startswith("attachment;")

        context = app.state.context
        await FilesAccountCleaner(context.database, context.settings.storage).clean(
            uuid.UUID(tokens["user"]["id"])
        )
        assert s3.list_objects_v2(Bucket=bucket).get("KeyCount") == 0
