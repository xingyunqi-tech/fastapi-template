"""文件存储：`STORAGE_DRIVER=local` 写入 `STORAGE_LOCAL_DIR`，`s3` 写入 S3 兼容对象存储。"""

import asyncio
import logging
import secrets
import shutil
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any, Final, Protocol, cast
from urllib.parse import quote
lazy from pathlib import Path

lazy import aioboto3
lazy from botocore.config import Config
lazy from botocore.session import get_session

from app.config import StorageDriver
lazy from app.config import StorageSettings

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager
    from typing import IO

logger = logging.getLogger("app.files")

KEY_BYTES: Final = 16


def new_key() -> str:
    return secrets.token_hex(KEY_BYTES)


def content_disposition(name: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(name, safe='')}"


class Storage(Protocol):
    async def put(self, key: str, body: IO[bytes], content_type: str) -> None: ...

    async def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, key: str) -> Path:
        """`key` 为 `new_key()` 生成的十六进制串。"""
        if not key.isalnum():
            raise ValueError("invalid storage key")
        return self.root / key

    async def put(self, key: str, body: IO[bytes], content_type: str) -> None:
        def write() -> None:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.path(key).open("wb") as target:
                shutil.copyfileobj(body, target)

        await asyncio.to_thread(write)

    async def delete(self, key: str) -> None:
        with suppress(FileNotFoundError):
            await asyncio.to_thread(self.path(key).unlink)


@dataclass(frozen=True, slots=True)
class S3Options:
    bucket: str
    region: str
    endpoint: str | None
    force_path_style: bool
    access_key_id: str | None
    secret_access_key: str | None

    @classmethod
    def from_settings(cls, settings: StorageSettings) -> S3Options:
        secret = settings.s3_secret_access_key
        return cls(
            bucket=settings.s3_bucket or "",
            region=settings.s3_region or "",
            endpoint=settings.s3_endpoint,
            force_path_style=settings.s3_force_path_style,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=secret.get_secret_value() if secret else None,
        )

    def client_kwargs(self) -> dict[str, Any]:
        """未配置访问密钥时使用默认凭据链。"""
        kwargs: dict[str, Any] = {
            "region_name": self.region,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if self.force_path_style else "virtual"},
            ),
        }
        if self.access_key_id and self.secret_access_key:
            kwargs["aws_access_key_id"] = self.access_key_id
            kwargs["aws_secret_access_key"] = self.secret_access_key
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


async def close_s3_clients() -> None:
    await _S3_CLIENTS.close()


@cache
def _signing_client(options: S3Options) -> Any:
    return get_session().create_client("s3", **options.client_kwargs())


class S3Storage:
    def __init__(self, options: S3Options) -> None:
        self.options = options

    async def put(self, key: str, body: IO[bytes], content_type: str) -> None:
        client = await _S3_CLIENTS.get(self.options)
        await client.upload_fileobj(
            body, self.options.bucket, key, ExtraArgs={"ContentType": content_type}
        )

    async def delete(self, key: str) -> None:
        client = await _S3_CLIENTS.get(self.options)
        await client.delete_object(Bucket=self.options.bucket, Key=key)

    def presigned_get(self, key: str, ttl_seconds: int, disposition: str | None) -> str:
        params: dict[str, Any] = {"Bucket": self.options.bucket, "Key": key}
        if disposition is not None:
            params["ResponseContentDisposition"] = disposition
        return str(
            _signing_client(self.options).generate_presigned_url(
                "get_object", Params=params, ExpiresIn=ttl_seconds
            )
        )


def build_storage(settings: StorageSettings) -> LocalStorage | S3Storage:
    if settings.driver is StorageDriver.S3:
        return S3Storage(S3Options.from_settings(settings))
    return LocalStorage(settings.local_dir)
