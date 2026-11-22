import pytest

from app.infrastructure import cache as cache_module
from app.infrastructure.cache import MemoryCache
from app.infrastructure.mail import RECENT_LIMIT, LoggingMailer, MailMessage, mask_email


@pytest.mark.parametrize(
    ("address", "masked"),
    [("john.doe@example.com", "j***@example.com"), ("invalid", "***"), ("@x.io", "***@x.io")],
)
def test_mask_email(address: str, masked: str) -> None:
    assert mask_email(address) == masked


async def test_logging_mailer_keeps_recent_messages() -> None:
    mailer = LoggingMailer()
    for index in range(RECENT_LIMIT + 5):
        await mailer.send(MailMessage("activation", f"u{index}@example.com", {}))
    assert len(mailer.sent) == RECENT_LIMIT
    assert mailer.sent[0].to == "u5@example.com"


async def test_memory_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_module, "MEMORY_MAX_ITEMS", 3)
    cache = MemoryCache()
    await cache.set("expired", 1, ttl_ms=-1)
    for key in ("a", "b", "c", "d"):
        await cache.set(key, key)
    assert [await cache.get(key) for key in ("expired", "a", "b", "c", "d")] == [
        None,
        None,
        "b",
        "c",
        "d",
    ]
