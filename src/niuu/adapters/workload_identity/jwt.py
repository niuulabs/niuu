"""JWT/JWKS workload identity verifier.

This adapter handles Kubernetes service account JWTs today and can also verify
SPIFFE JWT-SVIDs later by changing issuer/audience/JWKS configuration.
"""

from __future__ import annotations

import json
from typing import Any

import jwt
from jwt import PyJWKClient, PyJWKSet

from niuu.ports.workload_identity import WorkloadIdentityVerifier


class JwtWorkloadIdentityVerifier(WorkloadIdentityVerifier):
    """Verify JWT workload proofs using a remote or static JWKS."""

    def __init__(
        self,
        *,
        issuer: str = "",
        audiences: list[str] | str | None = None,
        jwks_uri: str = "",
        static_jwks: dict[str, Any] | str | None = None,
        algorithms: list[str] | None = None,
        insecure_skip_signature_verification: bool = False,
        **_extra: object,
    ) -> None:
        self._issuer = issuer
        if audiences is None:
            self._audiences: list[str] = []
        elif isinstance(audiences, str):
            self._audiences = [audiences]
        else:
            self._audiences = list(audiences)
        self._jwks_uri = jwks_uri
        if isinstance(static_jwks, str) and static_jwks.strip():
            self._static_jwks = json.loads(static_jwks)
        else:
            self._static_jwks = static_jwks if isinstance(static_jwks, dict) else None
        self._algorithms = algorithms or ["RS256", "ES256"]
        self._insecure_skip_signature_verification = insecure_skip_signature_verification
        self._client = PyJWKClient(jwks_uri) if jwks_uri else None

    async def verify(self, token: str) -> dict[str, Any]:
        options = {
            "verify_aud": bool(self._audiences),
            "verify_iss": bool(self._issuer),
            "verify_signature": not self._insecure_skip_signature_verification,
        }
        kwargs: dict[str, Any] = {"options": options, "algorithms": self._algorithms}
        if self._audiences:
            kwargs["audience"] = self._audiences
        if self._issuer:
            kwargs["issuer"] = self._issuer

        if self._insecure_skip_signature_verification:
            claims = jwt.decode(token, **kwargs)
            return dict(claims)

        key = self._resolve_key(token)
        claims = jwt.decode(token, key=key, **kwargs)
        return dict(claims)

    def _resolve_key(self, token: str) -> Any:
        if self._static_jwks is not None:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            jwk_set = PyJWKSet.from_dict(self._static_jwks)
            for key in jwk_set.keys:
                if key.key_id == kid or kid is None:
                    return key.key
            raise ValueError(f"No matching JWK found for kid={kid!r}")

        if self._client is None:
            raise ValueError("JwtWorkloadIdentityVerifier requires jwks_uri or static_jwks")
        return self._client.get_signing_key_from_jwt(token).key
