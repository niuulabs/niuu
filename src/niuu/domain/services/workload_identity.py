"""Workload identity exchange service."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import (
    VALKYRIE_BUILD_TOKEN_USE,
    bound_workload_scopes,
)
from niuu.ports.workload_identity import (
    IssuedWorkloadToken,
    WorkloadIdentityVerifier,
    WorkloadTokenIssuer,
)


@dataclass(frozen=True)
class WorkloadExchangeResult:
    """Issued token plus normalized principal metadata."""

    token: str
    expires_at: int
    principal: Principal
    workload_subject: str
    workload_name: str


class WorkloadIdentityError(ValueError):
    """Raised when a workload proof cannot be exchanged."""


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _claim(claims: dict[str, Any], path: str) -> Any:
    if path in claims:
        return claims[path]
    parts = path.split(".")
    current: Any = claims
    index = 0
    while index < len(parts):
        if not isinstance(current, dict):
            return None
        for end in range(len(parts), index, -1):
            key = ".".join(parts[index:end])
            if key in current:
                current = current[key]
                index = end
                break
        else:
            return None
    return current


class WorkloadIdentityService(WorkloadTokenIssuer):
    """Validate workload proofs and mint short-lived Volundr JWTs."""

    def __init__(
        self,
        config: Any,
        *,
        signing_key_pem: str = "",
        verifiers: dict[str, WorkloadIdentityVerifier] | None = None,
    ) -> None:
        self._config = config
        configured_pem = signing_key_pem or str(getattr(config, "signing_key_pem", "") or "")
        self._private_key = self._load_or_generate_key(configured_pem)
        self._verifiers = dict(verifiers or {})

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", False))

    def jwks(self) -> dict[str, Any]:
        public_numbers = self._private_key.public_key().public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": getattr(self._config, "key_id", "niuu-workload"),
                    "use": "sig",
                    "alg": "RS256",
                    "n": _b64url_uint(public_numbers.n),
                    "e": _b64url_uint(public_numbers.e),
                }
            ]
        }

    async def exchange(
        self,
        token: str,
        *,
        audiences: list[str] | None = None,
        scopes: list[str] | None = None,
    ) -> WorkloadExchangeResult:
        if not self.enabled:
            raise WorkloadIdentityError("Workload identity exchange is disabled")
        if not token.strip():
            raise WorkloadIdentityError("Missing workload token")

        build_scopes = bound_workload_scopes(scopes)
        last_error: Exception | None = None
        for mapping in getattr(self._config, "mappings", []) or []:
            verifier_name = getattr(mapping, "verifier", "kubernetes")
            verifier = self._verifiers.get(verifier_name)
            if verifier is None:
                last_error = WorkloadIdentityError(f"Verifier not configured: {verifier_name}")
                continue
            try:
                claims = await verifier.verify(token)
            except Exception as exc:
                last_error = exc
                continue
            if self._matches(mapping, claims):
                return self._issue(
                    mapping,
                    claims,
                    audiences=self._resolve_audiences(audiences),
                    build_scopes=build_scopes,
                )

        detail = f": {last_error}" if last_error else ""
        raise WorkloadIdentityError(f"No workload identity mapping matched{detail}")

    def _resolve_audiences(self, requested: list[str] | None) -> list[str]:
        allowed = [str(aud) for aud in (getattr(self._config, "audiences", []) or ["volundr-api"])]
        if not requested:
            return allowed

        normalized = [str(aud).strip() for aud in requested if str(aud).strip()]
        unknown = sorted(set(normalized) - set(allowed))
        if unknown:
            raise WorkloadIdentityError(
                "Requested workload audiences are not allowed: " + ", ".join(unknown)
            )
        return normalized or allowed

    def _matches(self, mapping: Any, claims: dict[str, Any]) -> bool:
        subject = str(getattr(mapping, "subject", "") or "").strip()
        claim_subject = str(claims.get("sub") or "")
        if subject and claim_subject != subject:
            return False
        subject_prefix = str(getattr(mapping, "subject_prefix", "") or "").strip()
        if subject_prefix and not claim_subject.startswith(subject_prefix):
            return False
        issuer = str(getattr(mapping, "issuer", "") or "").strip()
        if issuer and claims.get("iss") != issuer:
            return False
        for path, expected in (getattr(mapping, "claims", {}) or {}).items():
            if _claim(claims, str(path)) != expected:
                return False
        return True

    def _issue(
        self,
        mapping: Any,
        claims: dict[str, Any],
        *,
        audiences: list[str],
        build_scopes: list[str] | None = None,
    ) -> WorkloadExchangeResult:
        owner_id = str(getattr(mapping, "owner_id", "") or "").strip()
        if not owner_id:
            raise WorkloadIdentityError("Matched workload identity has no owner_id")
        tenant_id = str(getattr(mapping, "tenant_id", "") or "default").strip() or "default"
        roles = [str(role) for role in (getattr(mapping, "roles", []) or ["volundr:developer"])]
        workload_subject = str(claims.get("sub") or "")
        workload_name = str(getattr(mapping, "name", "") or workload_subject)
        email = str(getattr(mapping, "email", "") or "")
        principal = Principal(
            user_id=owner_id,
            email=email,
            tenant_id=tenant_id,
            roles=roles,
        )
        issued = self.issue_token(
            principal=principal,
            workload_subject=workload_subject,
            workload_name=workload_name,
            audiences=audiences,
            token_use=VALKYRIE_BUILD_TOKEN_USE if build_scopes else "",
            claims={
                **dict(getattr(mapping, "metadata", {}) or {}),
                **({"scopes": list(build_scopes)} if build_scopes else {}),
                "issuer": str(claims.get("iss") or ""),
            },
        )
        return WorkloadExchangeResult(
            token=issued.token,
            expires_at=issued.expires_at,
            principal=principal,
            workload_subject=workload_subject,
            workload_name=workload_name,
        )

    def issue_token(
        self,
        *,
        principal: Principal,
        workload_subject: str,
        workload_name: str,
        audiences: list[str],
        token_use: str = "",
        claims: dict[str, Any] | None = None,
    ) -> IssuedWorkloadToken:
        """Mint a workload JWT for a principal authenticated by another adapter."""
        if not self.enabled:
            raise WorkloadIdentityError("Workload identity exchange is disabled")
        resolved_audiences = self._resolve_audiences(audiences)
        now = int(time.time())
        expires_at = now + int(getattr(self._config, "token_ttl_seconds", 900))
        resource_access = {
            audience: {"roles": principal.roles}
            for audience in _resource_access_audiences(resolved_audiences)
        }
        payload: dict[str, Any] = {
            "iss": getattr(self._config, "issuer", "") or "niuu-workload",
            "sub": principal.user_id,
            "aud": resolved_audiences,
            "iat": now,
            "nbf": now - 5,
            "exp": expires_at,
            "jti": str(uuid4()),
            "typ": "Bearer",
            "azp": "niuu-workload-identity",
            "email": principal.email,
            "tenant_id": principal.tenant_id,
            "preferred_username": principal.user_id,
            "workload_sub": workload_subject,
            "workload_name": workload_name,
            "resource_access": resource_access,
        }
        extra_claims = dict(claims or {})
        workload_issuer = str(extra_claims.pop("issuer", ""))
        payload["workload_issuer"] = workload_issuer
        scopes = extra_claims.pop("scopes", None)
        for key, value in extra_claims.items():
            payload[f"workload_{key}"] = value

        if token_use:
            payload["token_use"] = token_use
        if scopes:
            payload["scopes"] = list(scopes)

        token = jwt.encode(
            payload,
            self._private_key,
            algorithm="RS256",
            headers={"kid": getattr(self._config, "key_id", "niuu-workload")},
        )
        return IssuedWorkloadToken(token=token, expires_at=expires_at)

    def _load_or_generate_key(self, pem: str) -> RSAPrivateKey:
        if pem:
            key = load_pem_private_key(pem.encode("utf-8"), password=None)
            if not isinstance(key, RSAPrivateKey):
                raise ValueError("Workload identity signing key must be an RSA private key")
            return key
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def private_key_pem_for_tests(self) -> str:
        """Return the generated private key. Intended for local tests only."""
        return (
            self._private_key.private_bytes(
                Encoding.PEM,
                PrivateFormat.PKCS8,
                NoEncryption(),
            )
            .decode("utf-8")
            .strip()
        )


def _resource_access_audiences(audiences: list[str]) -> list[str]:
    """Return role buckets for each accepted service audience and stable aliases."""
    values = {"volundr-api", "volundr"}
    for audience in audiences:
        audience = str(audience).strip()
        if not audience:
            continue
        values.add(audience)
        if audience.endswith("-api"):
            values.add(audience.removesuffix("-api"))
    return sorted(values)
