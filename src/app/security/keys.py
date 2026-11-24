"""Ed25519 密钥：PEM 加载、RFC 7638 指纹 `kid` 与 JWK。"""

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64decode_url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _raw(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def thumbprint(public_key: Ed25519PublicKey) -> str:
    """RFC 7638：必需成员按字典序、无空白序列化后取 SHA-256。"""
    members = {"crv": "Ed25519", "kty": "OKP", "x": b64url(_raw(public_key))}
    canonical = json.dumps(members, separators=(",", ":"), sort_keys=True).encode()
    return b64url(hashlib.sha256(canonical).digest())


def jwk(public_key: Ed25519PublicKey) -> dict[str, Any]:
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": b64url(_raw(public_key)),
        "kid": thumbprint(public_key),
        "alg": "EdDSA",
        "use": "sig",
    }


def load_private_key(pem: str) -> Ed25519PrivateKey:
    key = load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not an Ed25519 private key")
    return key


def load_public_key(pem: str) -> Ed25519PublicKey:
    key = load_pem_public_key(pem.encode())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("not an Ed25519 public key")
    return key


@dataclass(frozen=True, slots=True)
class SigningKey:
    private_key: Ed25519PrivateKey
    kid: str

    @classmethod
    def from_pem(cls, pem: str) -> SigningKey:
        private_key = load_private_key(pem)
        return cls(private_key, thumbprint(private_key.public_key()))


@dataclass(frozen=True, slots=True)
class KeyPair:
    private_pem: str
    public_pem: str


def generate_key_pair() -> KeyPair:
    private_key = Ed25519PrivateKey.generate()
    return KeyPair(
        private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode(),
        private_key.public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        .decode(),
    )
