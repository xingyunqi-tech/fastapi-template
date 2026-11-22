"""宽限期令牌对的加密：AES-256-GCM，密钥为 HKDF-SHA256(旧 refresh 明文)。"""

import hashlib
import json
import os
import secrets
from typing import Any, Final

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_INFO: Final = b"refresh-token-grace"
_NONCE_BYTES: Final = 12
REFRESH_TOKEN_BYTES: Final = 32


def new_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _key(refresh_token: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(
        refresh_token.encode()
    )


def seal(refresh_token: str, payload: dict[str, Any]) -> bytes:
    nonce = os.urandom(_NONCE_BYTES)
    plaintext = json.dumps(payload, separators=(",", ":")).encode()
    return nonce + AESGCM(_key(refresh_token)).encrypt(nonce, plaintext, None)


def unseal(refresh_token: str, sealed: bytes) -> dict[str, Any]:
    """密钥不符或数据被篡改时抛 `cryptography.exceptions.InvalidTag`。"""
    nonce, ciphertext = sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:]
    payload: dict[str, Any] = json.loads(
        AESGCM(_key(refresh_token)).decrypt(nonce, ciphertext, None)
    )
    return payload
