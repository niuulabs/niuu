"""Workload identity exchange service."""

from __future__ import annotations

import base64
import os
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
from niuu.ports.workload_identity import WorkloadIdentityVerifier
from niuu.utils import import_class, resolve_secret_kwargs


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


class WorkloadIdentityService:
    """Validate workload proofs and mint short-lived Volundr JWTs."""

    def __init__(self, config: Any) -> None:
        self._config = config
        self._private_key = self._load_or_generate_key()
        self._verifiers: dict[str, WorkloadIdentityVerifier] = {}
        for verifier_config in getattr(config, "verifiers", []) or []:
            kwargs = resolve_secret_kwargs(
                dict(getattr(verifier_config, "kwargs", {}) or {}),
                dict(getattr(verifier_config, "secret_kwargs_env", {}) or {}),
            )
            cls = import_class(getattr(verifier_config, "adapter"))
            self._verifiers[getattr(verifier_config, "name")] = cls(**kwargs)

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
    ) -> WorkloadExchangeResult:
        if not self.enabled:
            raise WorkloadIdentityError("Workload identity exchange is disabled")
        if not token.strip():
            raise WorkloadIdentityError("Missing workload token")

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
                return self._issue(mapping, claims, audiences=self._resolve_audiences(audiences))

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
        if subject and claims.get("sub") != subject:
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
    ) -> WorkloadExchangeResult:
        owner_id = str(getattr(mapping, "owner_id", "") or "").strip()
        if not owner_id:
            raise WorkloadIdentityError("Matched workload identity has no owner_id")
        tenant_id = str(getattr(mapping, "tenant_id", "") or "default").strip() or "default"
        roles = [str(role) for role in (getattr(mapping, "roles", []) or ["volundr:developer"])]
        now = int(time.time())
        expires_at = now + int(getattr(self._config, "token_ttl_seconds", 900))
        workload_subject = str(claims.get("sub") or "")
        workload_name = str(getattr(mapping, "name", "") or workload_subject)
        email = str(getattr(mapping, "email", "") or "")
        resource_access = {
            audience: {"roles": roles} for audience in _resource_access_audiences(audiences)
        }
        payload: dict[str, Any] = {
            "iss": getattr(self._config, "issuer", "") or "niuu-workload",
            "sub": owner_id,
            "aud": audiences,
            "iat": now,
            "nbf": now - 5,
            "exp": expires_at,
            "jti": str(uuid4()),
            "typ": "Bearer",
            "azp": "niuu-workload-identity",
            "email": email,
            "tenant_id": tenant_id,
            "preferred_username": owner_id,
            "workload_sub": workload_subject,
            "workload_name": workload_name,
            "workload_issuer": str(claims.get("iss") or ""),
            "resource_access": resource_access,
        }
        metadata = dict(getattr(mapping, "metadata", {}) or {})
        for key, value in metadata.items():
            payload[f"workload_{key}"] = value

        token = jwt.encode(
            payload,
            self._private_key,
            algorithm="RS256",
            headers={"kid": getattr(self._config, "key_id", "niuu-workload")},
        )
        return WorkloadExchangeResult(
            token=token,
            expires_at=expires_at,
            principal=Principal(
                user_id=owner_id,
                email=email,
                tenant_id=tenant_id,
                roles=roles,
            ),
            workload_subject=workload_subject,
            workload_name=workload_name,
        )

    def _load_or_generate_key(self) -> RSAPrivateKey:
        configured = str(getattr(self._config, "signing_key_pem", "") or "")
        env_name = str(getattr(self._config, "signing_key_env", "") or "")
        pem = configured or (os.environ.get(env_name) if env_name else "")
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
