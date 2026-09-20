"""RSA evidence signatures using the deployment/workload signing key material."""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from niuu.domain.evidence import EvidenceProvenance
from niuu.ports.evidence import EvidenceAuthenticator


class RsaEvidenceAuthenticator(EvidenceAuthenticator):
    """Sign receipts with an existing deployment key and verify trusted public keys."""

    def __init__(
        self,
        *,
        key_id: str,
        private_key_pem: str | bytes | None = None,
        private_key_pem_file: str | None = None,
        trusted_public_keys: dict[str, str | bytes] | None = None,
        producer_keys: dict[str, tuple[str, ...] | list[str]],
    ) -> None:
        if not key_id:
            raise ValueError("Evidence signing key ID is required")
        if private_key_pem and private_key_pem_file:
            raise ValueError("Configure one evidence private key source")
        if private_key_pem_file:
            private_key_pem = Path(private_key_pem_file).read_bytes()
        self._key_id = key_id
        self._producer_keys = {
            producer: frozenset(keys) for producer, keys in producer_keys.items()
        }
        if not self._producer_keys or any(
            not producer or not keys for producer, keys in self._producer_keys.items()
        ):
            raise ValueError("Evidence producers must be bound to trusted signing key IDs")
        self._private_key: rsa.RSAPrivateKey | None = None
        if private_key_pem:
            encoded = (
                private_key_pem.encode() if isinstance(private_key_pem, str) else private_key_pem
            )
            key = serialization.load_pem_private_key(encoded, password=None)
            if not isinstance(key, rsa.RSAPrivateKey):
                raise ValueError("Evidence signing key must be an RSA private key")
            self._private_key = key

        self._public_keys: dict[str, rsa.RSAPublicKey] = {}
        for trusted_id, pem in (trusted_public_keys or {}).items():
            encoded = pem.encode() if isinstance(pem, str) else pem
            key = serialization.load_pem_public_key(encoded)
            if not isinstance(key, rsa.RSAPublicKey):
                raise ValueError("Trusted evidence key must be an RSA public key")
            self._public_keys[trusted_id] = key
        if self._private_key is not None:
            self._public_keys.setdefault(key_id, self._private_key.public_key())

    def sign(self, payload: bytes, producer_id: str) -> EvidenceProvenance:
        if self._private_key is None:
            raise RuntimeError("This evidence authenticator has no private signing key")
        if self._key_id not in self._producer_keys.get(producer_id, ()):
            raise RuntimeError("Signing key is not authorized for this evidence producer")
        signature = self._private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
        return EvidenceProvenance(
            producer_id=producer_id,
            key_id=self._key_id,
            signature=base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        )

    def verify(self, payload: bytes, provenance: EvidenceProvenance) -> bool:
        key = self._public_keys.get(provenance.key_id)
        if key is None or provenance.key_id not in self._producer_keys.get(
            provenance.producer_id, ()
        ):
            return False
        padding_bytes = "=" * (-len(provenance.signature) % 4)
        try:
            signature = base64.urlsafe_b64decode(provenance.signature + padding_bytes)
            key.verify(signature, payload, padding.PKCS1v15(), hashes.SHA256())
        except (InvalidSignature, ValueError):
            return False
        return True
