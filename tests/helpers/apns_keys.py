"""Throwaway APNs signing keys for tests. Never a real key."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa


def generate_p8() -> str:
    """A fresh EC P-256 private key as PKCS#8 PEM, the format of an ``AuthKey_*.p8``."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def public_pem(private_pem: str) -> str:
    """The public half, to verify a JWT signed with ``private_pem``."""
    key = serialization.load_pem_private_key(private_pem.encode("ascii"), password=None)
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("ascii")
    )


def generate_rsa_pem() -> str:
    """A key of the wrong type: APNs needs EC P-256."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def generate_p384_pem() -> str:
    """An EC key on the wrong curve."""
    key = ec.generate_private_key(ec.SECP384R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def write_p8(directory: Path, name: str = "AuthKey_TESTKEY123.p8") -> Path:
    path = directory / name
    path.write_text(generate_p8(), encoding="ascii")
    return path
