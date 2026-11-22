import base64
import time
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid7

import jwt
import pytest
import time_machine
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.config.auth import AuthSettings
from app.identity.sessions.grace import hash_token, new_refresh_token, seal, unseal
from app.security import rbac
from app.security.jwt import InvalidTokenError, TokenExpiredError, TokenSigner, TokenVerifier
from app.security.keys import SigningKey, jwk, load_public_key, thumbprint
from tests import keys

USER = UUID("019a8f3c-6b1e-7d2a-8000-000000000001")
SESSION = UUID("019a8f3c-6b1e-7d2a-8000-000000000002")


def signer(pem: str = keys.SIGNING_KEY, ttl: timedelta = timedelta(minutes=15)) -> TokenSigner:
    return TokenSigner(
        SigningKey.from_pem(pem), issuer=keys.ISSUER, audience=keys.AUDIENCE, ttl=ttl
    )


def verifier(*pems: str) -> TokenVerifier:
    known = pems or (keys.PUBLIC_KEY,)
    return TokenVerifier.from_pems(known, issuer=keys.ISSUER, audience=keys.AUDIENCE)


def token(**overrides: Any) -> str:
    return signer().sign(
        sub=overrides.get("sub", USER),
        sid=SESSION,
        roles=overrides.get("roles", ["user"]),
        email_verified=True,
        auth_time=1_700_000_000,
    )


def forged(claims: dict[str, Any], headers: dict[str, Any] | None = None) -> str:
    kid = SigningKey.from_pem(keys.SIGNING_KEY).kid
    return jwt.encode(
        claims, keys.SIGNING_KEY, algorithm="EdDSA", headers={"kid": kid, **(headers or {})}
    )


def valid_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": keys.ISSUER,
        "aud": keys.AUDIENCE,
        "sub": str(USER),
        "sid": str(SESSION),
        "roles": ["user"],
        "email_verified": False,
        "auth_time": now,
        "iat": now,
        "exp": now + 60,
        "jti": "j",
    }
    claims.update(overrides)
    return claims


class TestKeys:
    def test_thumbprint_matches_rfc_8037_example(self) -> None:
        raw = base64.urlsafe_b64decode("11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo=")
        public_key = Ed25519PublicKey.from_public_bytes(raw)
        assert thumbprint(public_key) == "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k"

    def test_jwk_members(self) -> None:
        key = load_public_key(keys.PUBLIC_KEY)
        assert jwk(key) == {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": jwk(key)["x"],
            "kid": thumbprint(key),
            "alg": "EdDSA",
            "use": "sig",
        }
        assert SigningKey.from_pem(keys.SIGNING_KEY).kid == thumbprint(key)


class TestTokens:
    def test_round_trip(self) -> None:
        principal = verifier().verify(token(roles=["admin", "user"]))

        assert (principal.sub, principal.sid) == (USER, SESSION)
        assert (principal.roles, principal.email_verified) == (("admin", "user"), True)
        assert principal.auth_time == 1_700_000_000

    def test_header_has_alg_and_kid(self) -> None:
        header = jwt.get_unverified_header(token())
        assert header == {
            "alg": "EdDSA",
            "kid": SigningKey.from_pem(keys.SIGNING_KEY).kid,
            "typ": "JWT",
        }

    def test_rotated_key_is_accepted_when_listed(self) -> None:
        other = signer(keys.OTHER_SIGNING_KEY).sign(
            sub=USER, sid=SESSION, roles=[], email_verified=False, auth_time=0
        )
        assert verifier(keys.PUBLIC_KEY, keys.OTHER_PUBLIC_KEY).verify(other).sub == USER
        with pytest.raises(InvalidTokenError):
            verifier().verify(other)

    def test_unknown_or_missing_kid_is_rejected(self) -> None:
        unknown = forged(valid_claims(), {"kid": "unknown"})
        missing = jwt.encode(valid_claims(), keys.SIGNING_KEY, algorithm="EdDSA")
        for value in (unknown, missing):
            with pytest.raises(InvalidTokenError):
                verifier().verify(value)

    def test_other_algorithm_is_rejected(self) -> None:
        kid = SigningKey.from_pem(keys.SIGNING_KEY).kid
        hs = jwt.encode(valid_claims(), "x" * 32, algorithm="HS256", headers={"kid": kid})
        with pytest.raises(InvalidTokenError):
            verifier().verify(hs)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"aud": "other"},
            {"aud": [keys.AUDIENCE, "other"]},
            {"iss": "https://other.test"},
            {"sub": "7"},
            {"sid": None},
            {"roles": "user"},
            {"email_verified": "true"},
            {"auth_time": True},
        ],
    )
    def test_invalid_claims_are_rejected(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(InvalidTokenError):
            verifier().verify(forged(valid_claims(**overrides)))

    @pytest.mark.parametrize(
        "claim", ["iss", "aud", "sub", "sid", "roles", "email_verified", "auth_time", "exp"]
    )
    def test_missing_claim_is_rejected(self, claim: str) -> None:
        claims = valid_claims()
        del claims[claim]
        with pytest.raises(InvalidTokenError):
            verifier().verify(forged(claims))

    def test_expiry_allows_30_seconds_of_leeway(self) -> None:
        issued = token()
        expires = time.time() + 15 * 60
        with time_machine.travel(expires + 29, tick=False):
            assert verifier().verify(issued).sub == USER
        with time_machine.travel(expires + 31, tick=False), pytest.raises(TokenExpiredError):
            verifier().verify(issued)

    def test_expires_in_follows_ttl(self) -> None:
        short = signer(ttl=timedelta(minutes=5))
        claims = jwt.decode(
            short.sign(sub=USER, sid=SESSION, roles=[], email_verified=False, auth_time=0),
            keys.PUBLIC_KEY,
            algorithms=["EdDSA"],
            audience=keys.AUDIENCE,
        )
        assert short.expires_in == claims["exp"] - claims["iat"] == 300

    def test_subject_is_none_for_invalid_token(self) -> None:
        assert verifier().subject(token()) == USER
        assert verifier().subject("nope") is None


class TestGrace:
    def test_round_trip(self) -> None:
        refresh = new_refresh_token()
        sealed = seal(refresh, {"accessToken": "a", "refreshToken": "r", "expiresIn": 900})
        assert unseal(refresh, sealed) == {
            "accessToken": "a",
            "refreshToken": "r",
            "expiresIn": 900,
        }

    def test_other_token_or_tampering_fails(self) -> None:
        refresh = new_refresh_token()
        sealed = seal(refresh, {"a": 1})
        with pytest.raises(InvalidTag):
            unseal(new_refresh_token(), sealed)
        with pytest.raises(InvalidTag):
            unseal(refresh, sealed[:-1] + bytes([sealed[-1] ^ 1]))

    def test_refresh_token_is_opaque_32_bytes(self) -> None:
        refresh = new_refresh_token()
        assert len(base64.urlsafe_b64decode(refresh + "=")) == 32
        assert hash_token(refresh) != refresh
        assert len(hash_token(refresh)) == 64


class TestRbac:
    def test_admin_has_every_permission(self) -> None:
        assert set(rbac.permissions_of(rbac.Role.ADMIN)) == set(rbac.permission_codes())
        assert {p.value for p in rbac.Permission} <= set(rbac.permission_codes())

    def test_user_has_no_platform_permission(self) -> None:
        assert not {p.value for p in rbac.Permission} & set(rbac.permissions_of(rbac.Role.USER))

    def test_registered_permission_is_granted_to_roles(self) -> None:
        code = rbac.register(f"probe:{uuid7().hex}", roles=[rbac.Role.USER])
        assert code in rbac.permissions_of(rbac.Role.USER)
        assert code in rbac.permissions_for(["admin"])

    def test_unknown_role_grants_nothing(self) -> None:
        assert rbac.permissions_for(["owner"]) == set()


class TestAuthSettings:
    @staticmethod
    def load(**env: str) -> AuthSettings:
        return AuthSettings.model_validate(
            {"AUTH_ISSUER": "i", "AUTH_AUDIENCE": "a", "AUTH_SIGNING_KEY": keys.SIGNING_KEY, **env}
        )

    def test_escaped_newlines_are_accepted(self) -> None:
        settings = self.load(AUTH_SIGNING_KEY=keys.SIGNING_KEY.replace("\n", "\\n"))
        assert settings.signing_key.get_secret_value() == keys.SIGNING_KEY

    @pytest.mark.parametrize("value", ["secret", keys.PUBLIC_KEY])
    def test_signing_key_must_be_ed25519_private_key(self, value: str) -> None:
        with pytest.raises(ValueError, match="Ed25519 private key"):
            self.load(AUTH_SIGNING_KEY=value)

    def test_verify_keys_parse_pem_blocks(self) -> None:
        settings = self.load(AUTH_VERIFY_KEYS=keys.PUBLIC_KEY + keys.OTHER_PUBLIC_KEY)
        assert settings.verify_keys == [keys.PUBLIC_KEY.strip(), keys.OTHER_PUBLIC_KEY.strip()]
        with pytest.raises(ValueError, match="PEM public keys"):
            self.load(AUTH_VERIFY_KEYS="nope")

    @pytest.mark.parametrize("name", ["AUTH_ACCESS_TTL", "AUTH_REFRESH_TTL"])
    def test_ttl_must_be_positive(self, name: str) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            self.load(**{name: "0s"})


@pytest.mark.parametrize("password", ["x" * 7, "x" * 129])
def test_seed_admin_password_length_is_validated(password: str) -> None:
    with pytest.raises(ValueError, match="8 to 128"):
        TestAuthSettings.load(SEED_ADMIN_PASSWORD=password)
