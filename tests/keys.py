"""测试专用密钥，不得用于任何部署环境。"""

from functools import cache
from typing import Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

SIGNING_KEY: Final = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MC4CAQAwBQYDK2VwBCIEIIfsCwlN9KBxNWmO/r4h9O5HYHy12QqyuUnx96zHD0pL\n"
    "-----END PRIVATE KEY-----\n"
)
PUBLIC_KEY: Final = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEAnY2zrtsuXUKus+1eXytRTZDj4uNjsWLZ3aJgJlas00A=\n"
    "-----END PUBLIC KEY-----\n"
)
OTHER_SIGNING_KEY: Final = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MC4CAQAwBQYDK2VwBCIEID10cSByJospHGBRHPOPgQxbyX4aUeDGZx2sKMBkfoX7\n"
    "-----END PRIVATE KEY-----\n"
)
OTHER_PUBLIC_KEY: Final = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MCowBQYDK2VwAyEAlkvaP735i1zPT5Rhb1V/JhwkgTot4Upd1qsfSe3yjgg=\n"
    "-----END PUBLIC KEY-----\n"
)
ISSUER: Final = "https://identity.test"
AUDIENCE: Final = "test-api"


def _rsa_pair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private, public


@cache
def alipay_app_pair() -> tuple[str, str]:
    """应用密钥对（PKCS#8 私钥、SPKI 公钥），运行时生成。"""
    return _rsa_pair()


@cache
def alipay_platform_pair() -> tuple[str, str]:
    """模拟支付宝平台签名响应用的密钥对，运行时生成。"""
    return _rsa_pair()
