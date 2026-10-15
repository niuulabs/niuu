"""JWT/JWKS workload identity verifier.

This adapter handles Kubernetes service account JWTs today and can also verify
SPIFFE JWT-SVIDs later by changing issuer/audience/JWKS configuration.
"""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any

import jwt
from jwt import PyJWKClient, PyJWKSet

from niuu.ports.workload_identity import WorkloadIdentityVerifier


class JwtWorkloadIdentityVerifier(WorkloadIdentityVerifier):
    """Verify JWT workload proofs using a remote or static JWKS.

    ``timeout_seconds`` bounds each JWKS socket operation (default: five seconds).
    PyJWT retains ownership of key caching and rotation; remote I/O runs off-loop.
    """

    def __init__(
        self,
        *,
        issuer: str = "",
        audiences: list[str] | str | None = None,
        jwks_uri: str = "",
        static_jwks: dict[str, Any] | str | None = None,
        ca_cert_path: str = "",
        timeout_seconds: float = 5.0,
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
        ssl_context = None
        if ca_cert_path:
            ssl_context = ssl.create_default_context(cafile=ca_cert_path)
        if timeout_seconds <= 0:
            raise ValueError("JWKS timeout_seconds must be positive")
        self._client = (
            PyJWKClient(jwks_uri, ssl_context=ssl_context, timeout=timeout_seconds)
            if jwks_uri
            else None
        )

    async def verify(self, token: str) -> dict[str, Any]:
        # Unverified claims only reject unrelated proofs; they never grant trust.
        # Shared Kubernetes issuers still require trying each configured key set.
        if self._issuer:
            unverified = jwt.decode(token, options={"verify_signature": False})
            if unverified.get("iss") != self._issuer:
                raise jwt.InvalidIssuerError("Invalid issuer")
        if (
            not self._insecure_skip_signature_verification
            and jwt.get_unverified_header(token).get("alg") not in self._algorithms
        ):
            raise jwt.InvalidAlgorithmError("The specified alg value is not allowed")

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

        # PyJWKClient performs blocking urllib I/O on cache misses and rotation.
        # Never run it on the server loop: an unavailable issuer must not stall health.
        key = await asyncio.to_thread(self._resolve_key, token)
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
