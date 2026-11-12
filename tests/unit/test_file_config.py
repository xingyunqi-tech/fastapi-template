from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from app.config import FileDriver, Settings, SettingsError
from app.modules.files.purposes import (
    FilePurpose,
    file_extension,
    file_owner_for,
    mime_type_for,
    parse_file_purpose,
)
from app.modules.files.urls import FileUrls

S3_ENV = {
    "ACCESS_KEY_ID": "key",
    "SECRET_ACCESS_KEY": "secret",
    "AWS_DEFAULT_S3_BUCKET": "bucket",
    "AWS_S3_REGION": "us-east-1",
}


def test_defaults_match_nestjs() -> None:
    file = Settings.load().file

    assert file.driver is FileDriver.LOCAL
    assert file.force_path_style is True
    assert file.max_sizes == {
        "image": 5 * 1024 * 1024,
        "private-image": 10 * 1024 * 1024,
        "content-image": 5 * 1024 * 1024,
        "audio": 20 * 1024 * 1024,
        "import": 50 * 1024 * 1024,
        "export": 20 * 1024 * 1024,
    }
    assert file.max_file_size == 50 * 1024 * 1024


@pytest.mark.parametrize("driver", ["s3", "s3-presigned"])
def test_s3_drivers_require_credentials(monkeypatch: pytest.MonkeyPatch, driver: str) -> None:
    monkeypatch.setenv("FILE_DRIVER", driver)

    with pytest.raises(SettingsError, match="ACCESS_KEY_ID, SECRET_ACCESS_KEY"):
        Settings.load()


@pytest.mark.parametrize(
    ("name", "value"),
    [("FILE_DRIVER", "ftp"), ("FILE_IMAGE_MAX_SIZE", "0"), ("AWS_S3_FORCE_PATH_STYLE", "1")],
)
def test_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(SettingsError):
        Settings.load()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, FilePurpose.IMAGE),
        ("", FilePurpose.IMAGE),
        ("audio", FilePurpose.AUDIO),
        ("AUDIO", None),
        (["image"], None),
    ],
)
def test_parse_file_purpose(value: Any, expected: FilePurpose | None) -> None:
    assert parse_file_purpose(value) is expected


def test_mime_type_by_extension() -> None:
    assert file_extension("a.tar.JPEG") == "jpeg"
    assert file_extension("png") == ""
    assert mime_type_for(FilePurpose.IMAGE, "x.GIF") == "image/gif"
    assert mime_type_for(FilePurpose.PRIVATE_IMAGE, "x.gif") is None
    assert mime_type_for(FilePurpose.AUDIO, "x.m4a") == "audio/mp4"


def test_content_assets_have_no_owner() -> None:
    assert file_owner_for(FilePurpose.CONTENT_IMAGE, 7) is None
    assert file_owner_for(FilePurpose.IMPORT, 7) == 7


def _file(visibility: str, path: str = "/api/v1/files/a.png") -> Any:
    return SimpleNamespace(id="f1", path=path, visibility=visibility)


def test_local_urls() -> None:
    urls = FileUrls.from_settings(Settings.load())

    assert urls.build(_file("PUBLIC")) == "http://localhost/api/v1/files/a.png"
    assert urls.build(_file("PRIVATE")) == "http://localhost/api/v1/files/private/f1"


@pytest.mark.parametrize(
    ("endpoint", "prefix"),
    [
        (None, "https://bucket.s3.amazonaws.com/a.png?"),
        ("http://minio:9000", "http://minio:9000/bucket/a.png?"),
    ],
)
def test_s3_urls_are_presigned(
    monkeypatch: pytest.MonkeyPatch, endpoint: str | None, prefix: str
) -> None:
    monkeypatch.setenv("FILE_DRIVER", "s3")
    for name, value in S3_ENV.items():
        monkeypatch.setenv(name, value)
    if endpoint:
        monkeypatch.setenv("AWS_S3_ENDPOINT", endpoint)

    url = FileUrls.from_settings(Settings.load()).build(_file("PRIVATE", "a.png"))

    query = parse_qs(urlsplit(url).query)
    assert url.startswith(prefix)
    assert query["X-Amz-Expires"] == ["3600"]
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
