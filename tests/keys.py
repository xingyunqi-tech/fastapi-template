"""测试专用的 Ed25519 密钥对，不得用于任何部署环境。"""

from typing import Final

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
