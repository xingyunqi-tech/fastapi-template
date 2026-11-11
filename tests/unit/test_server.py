from typing import Any, ClassVar

lazy import pytest

from app import __main__ as server


class FakeGranian:
    instances: ClassVar[list[FakeGranian]] = []

    def __init__(self, target: str, **kwargs: Any) -> None:
        self.target = target
        self.kwargs = kwargs
        self.served = False
        FakeGranian.instances.append(self)

    def serve(self) -> None:
        self.served = True


def test_serves_app_on_configured_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "Granian", FakeGranian)
    monkeypatch.setenv("APP_PORT", "3200")
    monkeypatch.setenv("WEB_CONCURRENCY", "2")

    server.main()

    instance = FakeGranian.instances[-1]
    assert instance.target == "app.main:app"
    assert instance.kwargs["port"] == 3200
    assert instance.kwargs["workers"] == 2
    assert instance.served
