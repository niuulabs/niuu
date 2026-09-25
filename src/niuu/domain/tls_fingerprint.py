"""Pure validation for a Guild instance's pinned ``config.tls_fingerprint``.

Shared by the domain-level registration validator (``services/instances.py``)
and the outbound TLS-pinning adapter (``adapters/outbound/guild_transport.py``)
so both agree on what a well-formed pin looks like without either importing
the other's layer.
"""

from __future__ import annotations

import hashlib

_SHA256_HEX_LENGTH = hashlib.sha256().digest_size * 2


def normalize_tls_fingerprint(value: str) -> str:
    """Normalize a configured ``tls_fingerprint`` to a lowercase sha256 hex digest.

    Accepts the colon-separated form most TLS tooling prints (``AB:CD:...``)
    as well as a bare hex string. Raises ``ValueError`` for anything else —
    a malformed pin must fail registration loudly, not silently never match.
    """
    candidate = value.strip().lower().replace(":", "")
    if len(candidate) != _SHA256_HEX_LENGTH or any(c not in "0123456789abcdef" for c in candidate):
        raise ValueError(
            "tls_fingerprint must be a sha256 hex digest of the leaf certificate "
            "(64 hex characters, optionally colon-separated)"
        )
    return candidate
