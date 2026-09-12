"""In-memory token issuer for development and testing.

Issues JWTs signed with HS256 using a local key. NOT for production —
tokens won't be recognised by Envoy's IDP-based JWT filter.
Use this for local dev without an IDP, and the KeycloakTokenIssuer
(or another IDP adapter) in production.
"""

from __future__ import annotations

import hashlib
import logging
import time
from uuid import uuid4

import jwt

from niuu.domain.services.token_scope import validate_pat_scopes
from niuu.ports.token_issuer import IssuedToken, TokenIssuer

logger = logging.getLogger(__name__)


class MemoryTokenIssuer(TokenIssuer):
    """Issues HS256-signed JWTs locally for dev/test.

    Constructor kwargs:
        signing_key: Symmetric key for HS256 signing (required).
    """

    def __init__(self, *, signing_key: str = "dev-only-key", **_extra: object) -> None:
        if not signing_key:
            raise ValueError("MemoryTokenIssuer requires a signing_key")
        self._signing_key = signing_key

    async def issue_token(
        self,
        *,
        subject_token: str,
        name: str,
        ttl_days: int = 365,
        scopes: list[str] | None = None,
    ) -> IssuedToken:
        requested = validate_pat_scopes(scopes)
        # Extract sub from the subject_token if it's a JWT, else hash it
        sub = ""
        try:
            claims = jwt.decode(subject_token, options={"verify_signature": False})
            sub = claims.get("sub", "")
        except Exception:
            sub = (
                hashlib.sha256(subject_token.encode()).hexdigest()[:16] if subject_token else "dev"
            )

        now = int(time.time())
        jti = str(uuid4())
        payload = {
            "sub": sub,
            "type": "pat",
            "jti": jti,
            "name": name,
            "iat": now,
            "exp": now + ttl_days * 86400,
        }
        if requested is not None:
            payload["scope"] = " ".join(requested)
        raw_token = jwt.encode(payload, self._signing_key, algorithm="HS256")
        return IssuedToken(
            raw_token=raw_token,
            token_id=jti,
            subject=sub,
            expires_at=payload["exp"],
            scopes=requested,
        )

    async def close(self) -> None:
        pass
