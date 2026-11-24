import asyncio
from typing import Any
from uuid import UUID

import httpx
import pytest
from typer.testing import CliRunner

from app.cli import cli
from app.config import AuthRole, Settings, SettingsError, get_settings
from app.infrastructure.database.base import Branch
from app.infrastructure.database.branches import migration_targets, role_branches
from app.infrastructure.scheduler import SharedTaskSchedules
from app.security.jwks import JwksKeySet, JwksUnavailableError, max_age, parse_jwks
from app.security.keys import jwk, load_public_key
from tests import keys
from tests.conftest import JWKS_URL, use_production, use_verifier

PUBLIC = load_public_key(keys.PUBLIC_KEY)
OTHER = load_public_key(keys.OTHER_PUBLIC_KEY)


def _errors(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    with pytest.raises(SettingsError) as exc_info:
        Settings.load()
    return {error.split(":")[0] for error in exc_info.value.errors}


class TestRoleSettings:
    def test_signing_key_makes_the_issuer(self) -> None:
        settings = Settings.load()
        assert settings.auth.role is AuthRole.ISSUER
        assert role_branches(settings) == frozenset(Branch)
        assert migration_targets(settings) == ("heads",)

    def test_jwks_url_makes_a_verifier_without_database(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_verifier(monkeypatch)
        settings = Settings.load()
        assert settings.auth.role is AuthRole.VERIFIER
        assert not settings.database.enabled
        assert role_branches(settings) == frozenset()

    def test_verifier_with_database_migrates_platform_and_modules(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_verifier(monkeypatch)
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db?sslmode=disable")
        settings = Settings.load()
        assert role_branches(settings) == {Branch.PLATFORM, Branch.MODULES}
        assert migration_targets(settings) == ("platform@head", "modules@head")

    @pytest.mark.parametrize("both", [True, False])
    def test_exactly_one_of_signing_key_and_jwks_url(
        self, monkeypatch: pytest.MonkeyPatch, both: bool
    ) -> None:
        if both:
            monkeypatch.setenv("AUTH_JWKS_URL", JWKS_URL)
        else:
            monkeypatch.delenv("AUTH_SIGNING_KEY")
        with pytest.raises(SettingsError, match="AUTH_SIGNING_KEY"):
            Settings.load()

    def test_verify_keys_are_rejected_for_the_verifier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_verifier(monkeypatch)
        monkeypatch.setenv("AUTH_VERIFY_KEYS", keys.PUBLIC_KEY)
        with pytest.raises(SettingsError, match="AUTH_VERIFY_KEYS"):
            Settings.load()

    def test_issuer_requires_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATABASE_URL")
        assert _errors(monkeypatch) == {"DATABASE_URL"}

    def test_verifier_storage_requires_database_and_url_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_verifier(monkeypatch)
        monkeypatch.setenv("STORAGE_DRIVER", "local")
        assert _errors(monkeypatch) == {"DATABASE_URL", "STORAGE_URL_SECRET"}

    def test_production_verifier_does_not_require_issuer_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        use_production(monkeypatch)
        use_verifier(monkeypatch)
        for name in ("APP_FRONTEND_URL", "MAIL_HOST", "MAIL_FROM"):
            monkeypatch.delenv(name)
        assert Settings.load().auth.role is AuthRole.VERIFIER


class TestIssuerOnlyCommands:
    @pytest.mark.parametrize("args", [["seed"], ["users", "cleanup", str(UUID(int=1))]])
    def test_verifier_is_rejected(self, monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
        use_verifier(monkeypatch)
        get_settings.cache_clear()
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 1
        assert "只适用于签发方" in result.output


class TestSchedules:
    @pytest.mark.parametrize(("issuer", "count"), [(True, 2), (False, 0)])
    async def test_issuer_only_tasks_are_skipped_by_the_verifier(
        self, issuer: bool, count: int
    ) -> None:
        source = SharedTaskSchedules(issuer=issuer)
        await source.startup()
        schedules = await source.get_schedules()
        assert len(schedules) == count
        assert all("issuer_only" not in schedule.labels for schedule in schedules)


class FakeJwks:
    """计数请求次数的 JWKS 端点。"""

    def __init__(self, *jwks: dict[str, Any], cache_control: str = "max-age=300") -> None:
        self.body: dict[str, Any] = {"keys": list(jwks)}
        self.cache_control = cache_control
        self.requests = 0
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.fail:
            return httpx.Response(503)
        return httpx.Response(200, json=self.body, headers={"Cache-Control": self.cache_control})


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def key_set(server: FakeJwks, clock: Clock) -> JwksKeySet:
    return JwksKeySet(JWKS_URL, transport=httpx.MockTransport(server.handler), clock=clock)


class TestJwks:
    def test_parses_only_ed25519_signing_keys(self) -> None:
        rsa = {"kty": "RSA", "kid": "r", "n": "x", "e": "AQAB"}
        enc = {**jwk(OTHER), "use": "enc"}
        no_kid = {key: value for key, value in jwk(OTHER).items() if key != "kid"}
        parsed = parse_jwks({"keys": [jwk(PUBLIC), rsa, enc, no_kid, "x"]})
        assert list(parsed) == [jwk(PUBLIC)["kid"]]

    @pytest.mark.parametrize(
        ("header", "seconds"),
        [("max-age=60", 60), ("public, max-age=10, must-revalidate", 10), (None, 300)],
    )
    def test_max_age_follows_cache_control(self, header: str | None, seconds: int) -> None:
        assert max_age(header) == seconds

    async def test_keys_are_cached_until_max_age(self) -> None:
        server, clock = FakeJwks(jwk(PUBLIC), cache_control="max-age=60"), Clock()
        jwks = key_set(server, clock)
        kid = jwk(PUBLIC)["kid"]
        assert await jwks.get(kid) is not None
        clock.now += 59
        assert await jwks.get(kid) is not None
        assert server.requests == 1
        clock.now += 1
        await jwks.get(kid)
        assert server.requests == 2

    async def test_unknown_kid_refreshes_at_most_every_30_seconds(self) -> None:
        server, clock = FakeJwks(jwk(PUBLIC)), Clock()
        jwks = key_set(server, clock)
        other = jwk(OTHER)["kid"]
        assert await jwks.get(other) is None
        clock.now += 29
        assert await jwks.get(other) is None
        assert server.requests == 1
        server.body["keys"].append(jwk(OTHER))
        clock.now += 1
        assert await jwks.get(other) is not None
        assert server.requests == 2

    async def test_concurrent_requests_share_one_refresh(self) -> None:
        server, clock = FakeJwks(jwk(PUBLIC)), Clock()
        jwks = key_set(server, clock)
        kid = jwk(PUBLIC)["kid"]
        results = await asyncio.gather(*(jwks.get(kid) for _ in range(10)))
        assert all(result is not None for result in results)
        assert server.requests == 1

    async def test_failed_refresh_keeps_cached_keys(self) -> None:
        server, clock = FakeJwks(jwk(PUBLIC), cache_control="max-age=60"), Clock()
        jwks = key_set(server, clock)
        kid = jwk(PUBLIC)["kid"]
        await jwks.get(kid)
        server.fail = True
        clock.now += 61
        assert await jwks.get(kid) is not None

    async def test_unavailable_without_cached_keys(self) -> None:
        server, clock = FakeJwks(), Clock()
        server.fail = True
        with pytest.raises(JwksUnavailableError):
            await key_set(server, clock).get("kid")

    async def test_concurrent_requests_are_all_unavailable_without_keys(self) -> None:
        server, clock = FakeJwks(), Clock()
        server.fail = True
        jwks = key_set(server, clock)
        results = await asyncio.gather(*(jwks.get("kid") for _ in range(5)), return_exceptions=True)
        assert all(isinstance(result, JwksUnavailableError) for result in results)
        assert server.requests == 1

    async def test_failed_refresh_waits_30_seconds_before_retrying(self) -> None:
        server, clock = FakeJwks(jwk(PUBLIC)), Clock()
        server.fail = True
        jwks = key_set(server, clock)
        kid = jwk(PUBLIC)["kid"]
        for _ in range(3):
            with pytest.raises(JwksUnavailableError):
                await jwks.get(kid)
        assert server.requests == 1
        server.fail = False
        clock.now += 30
        assert await jwks.get(kid) is not None
        assert server.requests == 2

    async def test_zero_max_age_still_caches_for_30_seconds(self) -> None:
        server, clock = FakeJwks(jwk(PUBLIC), cache_control="max-age=0"), Clock()
        jwks = key_set(server, clock)
        kid = jwk(PUBLIC)["kid"]
        await jwks.get(kid)
        clock.now += 29
        await jwks.get(kid)
        assert server.requests == 1
