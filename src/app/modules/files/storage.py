"""文件存储位置：本地 `./files` 目录或 S3 兼容对象存储。"""

import asyncio
import logging
import secrets
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Final, cast

lazy import aioboto3
lazy from botocore.config import Config
lazy from botocore.session import get_session

from app.config import FileDriver
from app.modules.files.purposes import file_extension
lazy from app.config import FileSettings

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager
    from typing import IO


logger = logging.getLogger("app.files")

LOCAL_FILES_ROOT: Final = Path("files")
SIGNED_URL_EXPIRES_IN: Final = 3600


def local_file_url_path(api_prefix: str, file_name: str) -> str:
    return f"/{api_prefix}/v1/files/{file_name}"


def local_disk_path(path: str) -> Path:
    return LOCAL_FILES_ROOT / PurePosixPath(path).name


def random_key(file_name: str) -> str:
    return f"{secrets.token_hex(11)[:21]}.{file_extension(file_name)}"


@dataclass(frozen=True, slots=True)
class S3Options:
    bucket: str
    region: str
    endpoint: str | None
    force_path_style: bool
    access_key_id: str
    secret_access_key: str

    @classmethod
    def from_settings(cls, settings: FileSettings) -> S3Options:
        secret = settings.secret_access_key
        return cls(
            bucket=settings.bucket or "",
            region=settings.region or "",
            endpoint=settings.endpoint,
            force_path_style=settings.force_path_style,
            access_key_id=settings.access_key_id or "",
            secret_access_key=secret.get_secret_value() if secret else "",
        )

    def client_kwargs(self) -> dict[str, Any]:
        path_style = bool(self.endpoint) and self.force_path_style
        kwargs: dict[str, Any] = {
            "region_name": self.region,
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"} if path_style else {"addressing_style": "virtual"},
            ),
        }
        if self.endpoint:
            kwargs["endpoint_url"] = self.endpoint
        return kwargs


class S3Clients:
    """按事件循环缓存 aioboto3 客户端，首次使用时创建，close 时统一释放。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: dict[S3Options, Any] = {}
        self._stack = AsyncExitStack()
        self._lock = asyncio.Lock()

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._clients = {}
            self._stack = AsyncExitStack()
            self._lock = asyncio.Lock()

    async def get(self, options: S3Options) -> Any:
        self._bind_loop()
        client = self._clients.get(options)
        if client is not None:
            return client
        async with self._lock:
            client = self._clients.get(options)
            if client is None:
                session = await asyncio.to_thread(aioboto3.Session)
                factory = cast(
                    "AbstractAsyncContextManager[Any]",
                    session.client("s3", **options.client_kwargs()),
                )
                client = await self._stack.enter_async_context(factory)
                self._clients[options] = client
            return client

    async def close(self) -> None:
        if self._loop is not asyncio.get_running_loop():
            return
        stack, self._stack, self._clients = self._stack, AsyncExitStack(), {}
        await stack.aclose()


_S3_CLIENTS: Final = S3Clients()


async def s3_client(options: S3Options) -> Any:
    return await _S3_CLIENTS.get(options)


async def close_s3_clients() -> None:
    await _S3_CLIENTS.close()


@cache
def _signing_client(options: S3Options) -> Any:
    return get_session().create_client("s3", **options.client_kwargs())


def presigned_get_url(options: S3Options, key: str) -> str:
    return str(
        _signing_client(options).generate_presigned_url(
            "get_object",
            Params={"Bucket": options.bucket, "Key": key},
            ExpiresIn=SIGNED_URL_EXPIRES_IN,
        )
    )


def presigned_put_url(options: S3Options, key: str, content_length: int | float) -> str:
    return str(
        _signing_client(options).generate_presigned_url(
            "put_object",
            Params={"Bucket": options.bucket, "Key": key, "ContentLength": content_length},
            ExpiresIn=SIGNED_URL_EXPIRES_IN,
        )
    )


class FileStorage:
    """按 FILE_DRIVER 读写、删除存储对象；`path` 为 file 表中的值。"""

    def __init__(self, settings: FileSettings) -> None:
        self.settings = settings

    @property
    def is_local(self) -> bool:
        return self.settings.driver is FileDriver.LOCAL

    def s3_options(self) -> S3Options:
        return S3Options.from_settings(self.settings)

    async def put(self, key: str, body: IO[bytes], content_type: str) -> None:
        options = self.s3_options()
        client = await s3_client(options)
        await client.upload_fileobj(
            body, options.bucket, key, ExtraArgs={"ContentType": content_type}
        )

    async def read(self, path: str) -> bytes:
        if self.is_local:
            return local_disk_path(path).read_bytes()
        options = self.s3_options()
        client = await s3_client(options)
        output = await client.get_object(Bucket=options.bucket, Key=path)
        body: bytes = await output["Body"].read()
        return body

    async def delete(self, path: str) -> None:
        if self.is_local:
            with suppress(OSError):
                local_disk_path(path).unlink()
            return
        options = self.s3_options()
        client = await s3_client(options)
        await client.delete_object(Bucket=options.bucket, Key=path)

    async def delete_quietly(self, path: str) -> None:
        try:
            await self.delete(path)
        except Exception as error:
            logger.warning("Failed to delete stored file %s: %s", path, error)
