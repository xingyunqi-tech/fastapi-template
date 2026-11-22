import textwrap

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _pem(key: str, label: str) -> bytes:
    if "-----BEGIN" in key:
        return key.replace("\\n", "\n").encode()
    body = "\n".join(textwrap.wrap("".join(key.split()), 64))
    return f"-----BEGIN {label}-----\n{body}\n-----END {label}-----\n".encode()


def load_private_key(key: str) -> rsa.RSAPrivateKey:
    """接受 PKCS#8 PEM 或去掉首尾行的 base64。"""
    loaded = serialization.load_pem_private_key(_pem(key, "PRIVATE KEY"), password=None)
    if not isinstance(loaded, rsa.RSAPrivateKey):
        raise TypeError("not an RSA private key")
    return loaded


def load_public_key(key: str) -> rsa.RSAPublicKey:
    """接受 SPKI PEM 或去掉首尾行的 base64。"""
    loaded = serialization.load_pem_public_key(_pem(key, "PUBLIC KEY"))
    if not isinstance(loaded, rsa.RSAPublicKey):
        raise TypeError("not an RSA public key")
    return loaded
