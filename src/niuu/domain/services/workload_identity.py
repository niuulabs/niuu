"""Workload identity exchange service."""

from __future__ import annotations

import base64
import logging
import re
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

from niuu.domain.models import Principal, WorkloadDelegation
from niuu.domain.services.token_scope import (
    VALKYRIE_BUILD_SCOPES,
    VALKYRIE_BUILD_TOKEN_USE,
    bound_workload_scopes,
)
from niuu.ports.owner_tenant_resolver import OwnerTenantResolverPort
from niuu.ports.workload_delegations import WorkloadDelegationRepository
from niuu.ports.workload_identity import (
    IssuedWorkloadToken,
    WorkloadIdentityVerifier,
    WorkloadTokenIssuer,
    WorkloadTokenVerificationError,
)

logger = logging.getLogger(__name__)

DEFAULT_ISSUER = "niuu-workload"
#: Claims every token this service mints carries; verification requires them.
_REQUIRED_CLAIMS = ("exp", "iat", "iss", "aud", "sub")


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


#: ``owner_from`` value: the caller acts for the owner its delegation record names.
OWNER_FROM_DELEGATION = "delegation"

#: Characters a ``subject_prefix`` must end on, so it names a whole segment of the
#: subject: "...:resident-" admits "...:resident-<id>", while a bare "...:muninn" would
#: also admit "...:muninn-anything" (a different ServiceAccount).
_PREFIX_SEPARATORS = (":", "-", "/", ".", "_", "@")


def _mapping_problem(mapping: Any) -> str:
    """Why a mapping can never admit a caller, or '' when it is well formed.

    A malformed mapping is skipped (fail closed) rather than matched loosely: the
    exchange can only be as narrow as its loosest mapping.
    """
    name = str(getattr(mapping, "name", "") or "?")
    subject = str(getattr(mapping, "subject", "") or "").strip()
    subject_prefix = str(getattr(mapping, "subject_prefix", "") or "").strip()
    if not str(getattr(mapping, "issuer", "") or "").strip():
        return f"Workload identity mapping {name!r} has no issuer"
    if not subject and not subject_prefix:
        return f"Workload identity mapping {name!r} names neither subject nor subject_prefix"
    if subject_prefix and not subject_prefix.endswith(_PREFIX_SEPARATORS):
        return (
            f"Workload identity mapping {name!r} subject_prefix {subject_prefix!r} must end "
            f"on one of {' '.join(_PREFIX_SEPARATORS)} (use subject for one exact identity)"
        )
    owner_from = str(getattr(mapping, "owner_from", "") or "").strip()
    if owner_from and owner_from != OWNER_FROM_DELEGATION:
        return f"Workload identity mapping {name!r} has unknown owner_from {owner_from!r}"
    return ""


def _owner_from(mapping: Any) -> str:
    return str(getattr(mapping, "owner_from", "") or "").strip()


def _specificity(mapping: Any) -> tuple[int, int]:
    """Rank a matching mapping: an exact subject beats any prefix, a longer prefix a shorter."""
    if str(getattr(mapping, "subject", "") or "").strip():
        return (2, 0)
    return (1, len(str(getattr(mapping, "subject_prefix", "") or "").strip()))


class WorkloadIdentityService(WorkloadTokenIssuer):
    """Validate workload proofs and mint short-lived Volundr JWTs."""

    def __init__(
        self,
        config: Any,
        *,
        signing_key_pem: str = "",
        verifiers: dict[str, WorkloadIdentityVerifier] | None = None,
        tenant_resolver: OwnerTenantResolverPort | None = None,
        delegations: WorkloadDelegationRepository | None = None,
    ) -> None:
        self._config = config
        configured_pem = signing_key_pem or str(getattr(config, "signing_key_pem", "") or "")
        self._trusted_signing_configured = bool(configured_pem.strip())
        self._private_key = self._load_or_generate_key(configured_pem)
        self._verifiers = dict(verifiers or {})
        self._tenant_resolver = tenant_resolver
        self._delegations = delegations

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._config, "enabled", False))

    @property
    def trusted_signing_configured(self) -> bool:
        """Whether the key came from configuration shared with token verifiers."""
        return self._trusted_signing_configured

    @property
    def has_configured_key(self) -> bool:
        """True when the signing key came from configuration.

        Without one the key is generated per process, so every token it minted
        (Forge session credentials included) stops verifying when the process
        restarts. Same fact as :attr:`trusted_signing_configured`.
        """
        return self._trusted_signing_configured

    @property
    def issuer(self) -> str:
        return str(getattr(self._config, "issuer", "") or DEFAULT_ISSUER)

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

        build_scopes = bound_workload_scopes(scopes, allowed=VALKYRIE_BUILD_SCOPES)
        last_error: Exception | None = None
        # Each verifier checks the proof once; several mappings usually share one.
        verified: dict[str, dict[str, Any] | Exception] = {}
        candidates: list[
            tuple[tuple[int, int], Any, dict[str, Any], WorkloadDelegation | None]
        ] = []
        for mapping in getattr(self._config, "mappings", []) or []:
            problem = _mapping_problem(mapping)
            if problem:
                last_error = WorkloadIdentityError(problem)
                continue
            verifier_name = getattr(mapping, "verifier", "kubernetes")
            verifier = self._verifiers.get(verifier_name)
            if verifier is None:
                last_error = WorkloadIdentityError(f"Verifier not configured: {verifier_name}")
                continue
            if verifier_name not in verified:
                try:
                    verified[verifier_name] = await verifier.verify(token)
                except Exception as exc:
                    verified[verifier_name] = exc
            claims = verified[verifier_name]
            if isinstance(claims, Exception):
                last_error = claims
                continue
            if not self._matches(mapping, claims):
                continue
            delegation = None
            if _owner_from(mapping) == OWNER_FROM_DELEGATION:
                # Admit only a workload some owner has delegated to: without an
                # active record this mapping does not apply to the caller at all.
                delegation = await self._active_delegation(verifier_name, claims)
                if delegation is None:
                    last_error = WorkloadIdentityError(
                        f"No active delegation for {claims.get('sub')!r} on {verifier_name!r}"
                    )
                    continue
            candidates.append((_specificity(mapping), mapping, claims, delegation))

        if candidates:
            candidates.sort(key=lambda candidate: candidate[0], reverse=True)
            best, mapping, claims, delegation = candidates[0]
            tied = [str(getattr(c[1], "name", "") or "?") for c in candidates if c[0] == best]
            if len(tied) > 1:
                raise WorkloadIdentityError(
                    "Workload proof matches several equally specific identity mappings: "
                    + ", ".join(tied)
                )
            return await self._issue(
                mapping,
                claims,
                audiences=self._resolve_audiences(audiences),
                build_scopes=build_scopes,
                delegation=delegation,
            )

        detail = f": {last_error}" if last_error else ""
        raise WorkloadIdentityError(f"No workload identity mapping matched{detail}")

    async def _active_delegation(
        self, verifier_name: str, claims: dict[str, Any]
    ) -> WorkloadDelegation | None:
        subject = str(claims.get("sub") or "")
        if self._delegations is None or not subject:
            return None
        return await self._delegations.get_active(verifier_name, subject)

    def delegation_mapping_for(self, verifier: str, subject: str) -> str | None:
        """The ``owner_from: delegation`` mapping that would admit this workload, if any.

        Lets the delegation API refuse a record no mapping could ever use.
        """
        for mapping in getattr(self._config, "mappings", []) or []:
            if _mapping_problem(mapping) or _owner_from(mapping) != OWNER_FROM_DELEGATION:
                continue
            if str(getattr(mapping, "verifier", "kubernetes")) != verifier:
                continue
            exact = str(getattr(mapping, "subject", "") or "").strip()
            prefix = str(getattr(mapping, "subject_prefix", "") or "").strip()
            if (exact and subject == exact) or (prefix and subject.startswith(prefix)):
                return str(getattr(mapping, "name", "") or "?")
        return None

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
        if not self._owner_id_claim_pattern_compatible(mapping, claims):
            return False
        return True

    def _owner_id_claim_pattern_compatible(self, mapping: Any, claims: dict[str, Any]) -> bool:
        """A configured ``owner_id_claim_pattern`` the claim value does not
        match means this mapping does not apply to this caller at all — try
        the next mapping, rather than matching on ``subject_prefix`` alone
        and then blowing up in ``_resolve_owner_id``. This is what stops a
        broad prefix mapping (e.g. residentMapping's
        "system:serviceaccount:<ns>:resident-" matching every resident SA in
        the namespace) from shadowing a more specific mapping some
        non-UUID-named ServiceAccount (e.g. a Fleet-managed
        "resident-muninn" or "resident-ravn") is meant to match instead.

        A claim that is entirely MISSING (as opposed to present but pattern-
        mismatched) is a distinct, harder failure — the mapping names a
        claim this verifier's tokens never carry at all, which is a mapping
        misconfiguration, not "try the next one" — left to
        ``_resolve_owner_id``, which raises with the specific remedy.
        """
        claim_path = str(getattr(mapping, "owner_id_claim", "") or "").strip()
        pattern = str(getattr(mapping, "owner_id_claim_pattern", "") or "").strip()
        if not claim_path or not pattern:
            return True
        claim_value = _claim(claims, claim_path)
        if not claim_value:
            return True
        match = re.fullmatch(pattern, str(claim_value))
        return bool(match and match.groups())

    def _resolve_owner_id(self, mapping: Any, claims: dict[str, Any]) -> str:
        """Fixed mapping.owner_id, or a per-caller id derived from a claim.

        A mapping with ``owner_id_claim`` set gives every distinct caller
        (e.g. a distinct Kubernetes ServiceAccount subject) its own principal
        instead of conflating every caller that matches the mapping's
        subject/subject_prefix into one shared identity — the same mapping a
        whole namespace of resident pods matches would otherwise mint the
        identical owner_id for every resident, making per-resident
        authorization (budget spend, trigger ownership) impossible.
        """
        claim_path = str(getattr(mapping, "owner_id_claim", "") or "").strip()
        if not claim_path:
            owner_id = str(getattr(mapping, "owner_id", "") or "").strip()
            if not owner_id:
                raise WorkloadIdentityError("Matched workload identity has no owner_id")
            return owner_id

        claim_value = _claim(claims, claim_path)
        if not claim_value:
            raise WorkloadIdentityError(
                f"Matched workload identity requires claim {claim_path!r} for "
                "owner_id, but the verified proof does not carry it"
            )
        claim_value = str(claim_value)

        pattern = str(getattr(mapping, "owner_id_claim_pattern", "") or "").strip()
        if not pattern:
            return claim_value

        # fullmatch, not match: a pattern missing a trailing "$" (an easy
        # mistake to make) must not silently accept a claim value with
        # unmatched trailing content — the unmatched suffix could be
        # anything, including another principal's identity fragment.
        match = re.fullmatch(pattern, claim_value)
        if match is None or not match.groups():
            raise WorkloadIdentityError(
                f"Claim {claim_path!r} value {claim_value!r} did not match "
                f"owner_id_claim_pattern {pattern!r}"
            )
        return match.group(1)

    async def _resolve_tenant_id(self, mapping: Any, owner_id: str) -> str:
        """Static mapping.tenant_id, or the owner's real tenant when a
        resolver is configured and this mapping derives owner_id per-caller.

        A per-caller (``owner_id_claim``) mapping's own ``tenant_id`` is one
        fixed guess shared by every caller it matches — correct for at most
        one tenant. When a resolver is wired, it is the authority for that
        case; a caller whose owner_id it cannot find is rejected outright
        rather than silently placed in the mapping's default tenant, which
        would put its triggers/spend where the real owner can never see them
        (GET /api/v1/ravn/budget/{id} keyed by the real tenant).
        """
        configured = str(getattr(mapping, "tenant_id", "") or "default").strip() or "default"
        claim_path = str(getattr(mapping, "owner_id_claim", "") or "").strip()
        if not claim_path or self._tenant_resolver is None:
            return configured
        resolved = await self._tenant_resolver.tenant_id_for_owner(owner_id)
        if resolved is None:
            raise WorkloadIdentityError(
                f"tenant_resolver has no durable record for owner_id {owner_id!r} — "
                "cannot derive this caller's real tenant_id"
            )
        return resolved

    async def _issue(
        self,
        mapping: Any,
        claims: dict[str, Any],
        *,
        audiences: list[str],
        build_scopes: list[str] | None = None,
        delegation: WorkloadDelegation | None = None,
    ) -> WorkloadExchangeResult:
        if delegation is not None:
            owner_id = delegation.owner_id
            tenant_id = delegation.tenant_id or "default"
        else:
            owner_id = self._resolve_owner_id(mapping, claims)
            tenant_id = await self._resolve_tenant_id(mapping, owner_id)
        roles = [str(role) for role in (getattr(mapping, "roles", []) or ["volundr:developer"])]
        workload_subject = str(claims.get("sub") or "")
        workload_name = str(
            (delegation.name if delegation else "")
            or getattr(mapping, "name", "")
            or workload_subject
        )
        email = str(getattr(mapping, "email", "") or "")
        principal = Principal(
            user_id=owner_id,
            email=email,
            tenant_id=tenant_id,
            roles=roles,
        )
        # True only when this mapping derived owner_id per-caller
        # (owner_id_claim) rather than a fixed value every caller matching
        # this mapping shares. Callers that need a real per-caller identity
        # (e.g. a resident reporting its own budget) can check
        # workload_owner_scoped on the issued token rather than silently
        # trusting a possibly-shared owner_id.
        owner_scoped = bool(str(getattr(mapping, "owner_id_claim", "") or "").strip())
        # The exchange is the one place that knows which workload a token speaks for:
        # record it, so a request made "as" the owner can be traced to its actor.
        logger.info(
            "Workload exchange: mapping=%s workload=%s owner=%s tenant=%s roles=%s audiences=%s",
            workload_name,
            workload_subject,
            owner_id,
            tenant_id,
            ",".join(roles),
            ",".join(audiences),
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
                "owner_scoped": owner_scoped,
                **(
                    {"delegation_id": str(delegation.id), "resident_id": delegation.resident_id}
                    if delegation is not None
                    else {}
                ),
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
        ttl_seconds: int | None = None,
    ) -> IssuedWorkloadToken:
        """Mint a workload JWT for a principal authenticated by another adapter."""
        if not self.enabled:
            raise WorkloadIdentityError("Workload identity exchange is disabled")
        resolved_audiences = self._resolve_audiences(audiences)
        now = int(time.time())
        lifetime = (
            ttl_seconds
            if ttl_seconds is not None
            else int(getattr(self._config, "token_ttl_seconds", 900))
        )
        expires_at = now + lifetime
        resource_access = {
            audience: {"roles": principal.roles}
            for audience in _resource_access_audiences(resolved_audiences)
        }
        payload: dict[str, Any] = {
            "iss": self.issuer,
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
        # RFC 8693 actor claim: the workload acting on behalf of ``sub``. Absent when the
        # token speaks for its subject directly (the subject is its own actor).
        if workload_subject and workload_subject != principal.user_id:
            payload["act"] = {"sub": workload_subject}
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

    def verify_token(self, token: str) -> dict[str, Any]:
        """Verify a token this service minted: RS256 signature, expiry, issuer, audience."""
        try:
            return jwt.decode(
                token,
                self._private_key.public_key(),
                algorithms=["RS256"],
                audience=self._resolve_audiences(None),
                issuer=self.issuer,
                options={"require": list(_REQUIRED_CLAIMS)},
            )
        except jwt.InvalidTokenError as exc:
            raise WorkloadTokenVerificationError(str(exc)) from exc

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
