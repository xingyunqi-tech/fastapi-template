from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid7

import pytest

from app.config import Settings, SettingsError, StorageDriver
from app.modules.files.purposes import MAX_SIZE, RULES, Purpose, sniff
from app.modules.files.storage import LocalStorage, S3Storage, content_disposition
from app.modules.files.urls import FileUrls

S3_ENV = {"STORAGE_DRIVER": "s3", "S3_BUCKET": "bucket", "S3_REGION": "us-east-1"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 12
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "
GIF = b"GIF89a" + b"\x00" * 10
PDF = b"%PDF-1.7\n" + b"\x00" * 7


def _file(content_type: str, name: str = "a b.pdf") -> Any:
    return SimpleNamespace(id=uuid7(), storage_key="k", content_type=content_type, name=name)


def test_storage_defaults() -> None:
    storage = Settings.load().storage

    assert storage.driver is StorageDriver.LOCAL
    assert storage.local_dir == Path("var/files")
    assert storage.url_ttl.total_seconds() == 600
    assert storage.s3_force_path_style is False


def test_s3_requires_bucket_and_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_DRIVER", "s3")

    with pytest.raises(SettingsError) as raised:
        Settings.load()

    assert "S3_BUCKET, S3_REGION: required when STORAGE_DRIVER=s3" in str(raised.value)


def test_s3_without_access_keys_uses_default_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in S3_ENV.items():
        monkeypatch.setenv(name, value)

    storage = FileUrls.from_settings(Settings.load()).storage

    assert isinstance(storage, S3Storage)
    assert "aws_access_key_id" not in storage.options.client_kwargs()


def test_avatar_rule_follows_convention() -> None:
    avatar = RULES[Purpose.AVATAR]

    assert avatar.content_types == {"image/jpeg", "image/png", "image/webp"}
    assert avatar.max_size == 5 * 1024 * 1024
    assert max(rule.max_size for rule in RULES.values()) == MAX_SIZE


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (WEBP, "image/webp"),
        (GIF, "image/gif"),
        (PDF, "application/pdf"),
        (b"<svg xmlns=", None),
        (b"", None),
    ],
)
def test_sniff_by_magic_bytes(head: bytes, expected: str | None) -> None:
    assert sniff(head) == expected


def test_local_url_is_signed_and_expires() -> None:
    urls = FileUrls(LocalStorage(Path("var")), b"secret", 600)
    file = _file("image/png")

    url = urlsplit(urls.build(file, now=1_000))
    query = {key: values[0] for key, values in parse_qs(url.query).items()}

    assert url.path == f"/api/v1/files/{file.id}/content"
    assert query["expires"] == "1600"
    assert urls.verify(file.id, 1600, query["signature"], now=1_600)
    assert not urls.verify(file.id, 1600, query["signature"], now=1_601)
    assert not urls.verify(file.id, 1601, query["signature"], now=1_000)
    assert not urls.verify(uuid7(), 1600, query["signature"], now=1_000)


def test_s3_url_is_presigned_with_attachment_for_non_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {**S3_ENV, "S3_ACCESS_KEY_ID": "k", "S3_SECRET_ACCESS_KEY": "s"}.items():
        monkeypatch.setenv(name, value)
    urls = FileUrls.from_settings(Settings.load())

    pdf = parse_qs(urlsplit(urls.build(_file("application/pdf"))).query)
    image = parse_qs(urlsplit(urls.build(_file("image/png"))).query)

    assert pdf["response-content-disposition"] == [content_disposition("a b.pdf")]
    assert pdf["X-Amz-Expires"] == ["600"]
    assert "response-content-disposition" not in image


def test_local_storage_rejects_unsafe_keys(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    with pytest.raises(ValueError, match="invalid storage key"):
        storage.path("../etc/passwd")
