"""In-process JWT bearer verification for hosts without an Envoy JWT filter.

Mini mode and single-host docker mode have no Envoy sidecar in front of them,
so nothing validates a bearer JWT's signature before it reaches the
application. ``EnvoyHeaderIdentityAdapter`` and ``EnvoyHeaderAuthenticationAdapter``
trust ``x-auth-*`` headers unconditionally because Envoy is assumed to have
already verified the token and stripped caller-supplied headers — on a host
without Envoy that assumption is false, and trusting those headers directly
would let any caller assert an arbitrary identity.

These adapters close that gap by verifying the bearer JWT's signature against
the issuer's published JWKS in-process, with the same claim semantics Envoy's
``jwt_authn`` filter and the header adapters use (``sub``, ``email``, a
configurable tenant claim, and a configurable — possibly dotted — roles
claim). Only a signature-verified ``Authorization: Bearer`` token can assert
identity; any ``x-auth-*`` header present on the request is ignored.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
from jwt import PyJWK

from identity.adapters.identity import EnvoyHeaderIdentityAdapter
from identity.models import Principal
from identity.ports import AuthorizationEvaluationError
from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError

logger = logging.getLogger(__name__)

#: Defaults mirror the Envoy jwt_authn filter's own defaults (see
#: charts/volundr/values.yaml envoy.jwt.{jwksTimeout,jwksCacheDurationSeconds}).
DEFAULT_CLOCK_LEEWAY_SECONDS = 60
DEFAULT_JWKS_CACHE_TTL_SECONDS = 300
DEFAULT_JWKS_TIMEOUT_SECONDS = 5.0
#: Minimum time between forced JWKS refreshes for the same issuer. An
#: unrecognised `kid` forces exactly one refresh per window rather than one
#: per request — otherwise a caller can force a fetch on demand (a probe of
#: N forged tokens with distinct/missing kids would otherwise cost N+1 fetches).
DEFAULT_MIN_REFRESH_INTERVAL_SECONDS = 5.0

#: Only asymmetric algorithms are ever trusted for a fetched JWK. A `kty:
#: "oct"` (symmetric) key would let anyone who can read the JWKS response
#: forge a token — that response is public, so a symmetric key published in
#: it protects nothing. Restricting to the algorithm PyJWT derives from the
#: key's own published kty/crv also rules out algorithm-confusion attacks
#: (a token claiming HS256 while an RSA public key is on file is never
#: accepted, because the key's own algorithm_name — not the token's alg
#: header — selects what `jwt.decode` is asked to verify with).
_REJECTED_KEY_TYPES = frozenset({"oct"})

_NO_ISSUERS_REMEDY = (
    "JwksBearerAuthenticationAdapter requires at least one entry in "
    "auth.oidc.issuers (issuer + audiences). Configure an OIDC issuer, or "
    "set auth.mode: none to run this host without authentication."
)


def _is_localhost(url: str) -> bool:
    return urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}


def _require_https(url: str, *, what: str) -> None:
    """Same posture as RemoteHeaderAuthenticationAdapter: HTTPS only, with an
    explicit localhost exception for a JWKS/discovery endpoint run on the
    same dev host (e.g. a local Keycloak in docker-compose)."""
    scheme = urlsplit(url).scheme
    if scheme == "https":
        return
    if scheme == "http" and _is_localhost(url):
        return
    raise ValueError(f"{what} must use HTTPS (got {url!r}); http:// is only allowed for localhost")


def _get_claim(claims: dict[str, Any], dotted_path: str) -> Any:
    """Walk a dotted claim path, e.g. ``resource_access.volundr.roles``."""
    value: Any = claims
    for part in dotted_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _claim_to_roles(value: Any) -> list[str]:
    """Normalise a roles claim the same way Envoy's claim_to_headers does.

    Envoy forwards a string claim as-is and a list claim comma-joined; the
    header adapters then split on commas (see
    ``niuu.adapters.identity_headers.parse_roles_header``). Read directly
    from the verified claim instead of round-tripping through a string.
    """
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


class _IssuerConfig:
    """One trusted issuer: plain data, validated once at construction."""

    __slots__ = ("issuer", "audiences", "jwks_uri")

    def __init__(
        self, *, issuer: str = "", audiences: list[str] | None = None, jwks_uri: str = ""
    ) -> None:
        if not issuer:
            raise ValueError("Each auth.oidc.issuers entry requires 'issuer'")
        if not audiences:
            raise ValueError(f"auth.oidc issuer {issuer!r} requires at least one audience")
        _require_https(issuer, what=f"auth.oidc issuer {issuer!r}")
        if jwks_uri:
            _require_https(jwks_uri, what=f"auth.oidc issuer {issuer!r} jwks_uri")
        self.issuer = issuer
        self.audiences = list(audiences)
        self.jwks_uri = jwks_uri


class JwksBearerAuthenticationAdapter(HeaderAuthenticationPort):
    """Validate a bearer JWT's signature against issuer JWKS in-process.

    Constructor kwargs (dynamic adapter pattern — plain values only):
        issuers: list of ``{"issuer": ..., "audiences": [...], "jwks_uri": ...}``.
            ``jwks_uri`` may be omitted; it is then resolved once via OIDC
            discovery (``<issuer>/.well-known/openid-configuration``).
        clock_leeway_seconds: allowed clock skew for ``exp``/``nbf``.
        jwks_cache_ttl_seconds: how long a fetched JWKS is cached before a
            routine refresh; an unrecognised ``kid`` always forces one
            immediate refresh regardless of this TTL (key rotation).
        jwks_timeout_seconds: HTTP timeout for discovery and JWKS fetches.
        min_refresh_interval_seconds: minimum time between two *forced*
            refreshes of the same issuer's JWKS (an unrecognised kid always
            forces one, but at most once per window — see the module-level
            constant's docstring for why).
        user_id_claim, email_claim, tenant_claim, roles_claim: claim names
            (roles_claim may be dotted) — default to the same claims Envoy's
            jwt_authn filter maps today (see charts/volundr/values.yaml).
        role_mapping: optional raw-claim-value -> platform-role mapping,
            applied the same way ``EnvoyHeaderAuthenticationAdapter`` does.

    Never falls back to an unverified read of the token, and never trusts
    caller-supplied ``x-auth-*`` headers — only the verified claims of the
    ``Authorization`` bearer token can assert identity. Every issuer and
    (once resolved) jwks_uri must be HTTPS, except localhost for local dev.
    """

    def __init__(
        self,
        *,
        issuers: list[dict[str, Any]] | None = None,
        clock_leeway_seconds: int = DEFAULT_CLOCK_LEEWAY_SECONDS,
        jwks_cache_ttl_seconds: int = DEFAULT_JWKS_CACHE_TTL_SECONDS,
        jwks_timeout_seconds: float = DEFAULT_JWKS_TIMEOUT_SECONDS,
        min_refresh_interval_seconds: float = DEFAULT_MIN_REFRESH_INTERVAL_SECONDS,
        user_id_claim: str = "sub",
        email_claim: str = "email",
        tenant_claim: str = "tenant_id",
        roles_claim: str = "resource_access.volundr.roles",
        role_mapping: dict[str, str] | None = None,
        **_extra: object,
    ) -> None:
        if not issuers:
            raise ValueError(_NO_ISSUERS_REMEDY)
        configs = [_IssuerConfig(**issuer) for issuer in issuers]
        self._issuers: dict[str, _IssuerConfig] = {cfg.issuer: cfg for cfg in configs}
        self._clock_leeway_seconds = clock_leeway_seconds
        self._jwks_cache_ttl_seconds = jwks_cache_ttl_seconds
        self._jwks_timeout_seconds = jwks_timeout_seconds
        self._min_refresh_interval_seconds = min_refresh_interval_seconds
        self._user_id_claim = user_id_claim
        self._email_claim = email_claim
        self._tenant_claim = tenant_claim
        self._roles_claim = roles_claim
        self._role_mapping = role_mapping
        # issuer -> ({kid: PyJWK}, fetched_at_monotonic)
        self._jwks_cache: dict[str, tuple[dict[str, PyJWK], float]] = {}
        self._last_forced_refresh: dict[str, float] = {}
        # Single-flight: concurrent requests for the same unresolved kid must
        # not each open their own JWKS fetch.
        self._refresh_lock = asyncio.Lock()

    async def _discover_jwks_uri(self, client: httpx.AsyncClient, issuer: str) -> str:
        discovery_url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        try:
            response = await client.get(discovery_url, timeout=self._jwks_timeout_seconds)
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthorizationEvaluationError(
                f"OIDC discovery for issuer {issuer!r} failed"
            ) from exc
        discovered_issuer = str(document.get("issuer") or "")
        if discovered_issuer != issuer:
            raise AuthorizationEvaluationError(
                f"OIDC discovery document for {issuer!r} declares issuer "
                f"{discovered_issuer!r} — refusing a mismatched authority"
            )
        jwks_uri = str(document.get("jwks_uri") or "")
        if not jwks_uri:
            raise AuthorizationEvaluationError(
                f"OIDC discovery for issuer {issuer!r} returned no jwks_uri; "
                "configure auth.oidc.issuers[].jwks_uri explicitly"
            )
        try:
            _require_https(jwks_uri, what=f"discovered jwks_uri for issuer {issuer!r}")
        except ValueError as exc:
            # The discovery document itself is untrusted input; treat an
            # insecure jwks_uri in it as a runtime evaluation failure, the
            # same as the issuer mismatch above — not a static config error.
            raise AuthorizationEvaluationError(str(exc)) from exc
        return jwks_uri

    async def _fetch_jwks(self, issuer_cfg: _IssuerConfig) -> dict[str, PyJWK]:
        try:
            async with httpx.AsyncClient() as client:
                jwks_uri = issuer_cfg.jwks_uri or await self._discover_jwks_uri(
                    client, issuer_cfg.issuer
                )
                response = await client.get(jwks_uri, timeout=self._jwks_timeout_seconds)
                response.raise_for_status()
                jwk_set = response.json()
        except AuthorizationEvaluationError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthorizationEvaluationError(
                f"JWKS fetch for issuer {issuer_cfg.issuer!r} failed"
            ) from exc
        # Discovery only ever needs to run once per issuer: cache the
        # resolved jwks_uri on the (shared, mutated-in-place) issuer config.
        if not issuer_cfg.jwks_uri:
            issuer_cfg.jwks_uri = jwks_uri
        keys: dict[str, PyJWK] = {}
        for raw_key in jwk_set.get("keys", []):
            if raw_key.get("kty") in _REJECTED_KEY_TYPES:
                # A symmetric key published in a JWKS response protects
                # nothing — the response itself is public.
                continue
            if raw_key.get("use") == "enc":
                # An encryption key was never meant to verify a signature.
                continue
            try:
                jwk = PyJWK.from_dict(raw_key)
            except (jwt.InvalidKeyError, jwt.PyJWKError):
                continue
            if jwk.key_id:
                keys[jwk.key_id] = jwk
        return keys

    async def _keys_for_issuer(
        self, issuer_cfg: _IssuerConfig, *, force_refresh: bool = False
    ) -> dict[str, PyJWK]:
        def _fresh(cached: tuple[dict[str, PyJWK], float] | None, now: float) -> bool:
            return cached is not None and now - cached[1] < self._jwks_cache_ttl_seconds

        def _throttled(now: float) -> bool:
            last_forced = self._last_forced_refresh.get(issuer_cfg.issuer, 0.0)
            return now - last_forced < self._min_refresh_interval_seconds

        cached = self._jwks_cache.get(issuer_cfg.issuer)
        now = time.monotonic()
        if not force_refresh and _fresh(cached, now):
            return cached[0]
        if force_refresh and cached is not None and _throttled(now):
            # An unrecognised kid forces at most one refresh per window —
            # otherwise a caller can trigger a fetch on demand by sending
            # tokens with distinct or missing kids.
            return cached[0]

        async with self._refresh_lock:
            # Re-check after acquiring the lock: a concurrent caller may
            # already have refreshed while this one waited (single-flight).
            cached = self._jwks_cache.get(issuer_cfg.issuer)
            now = time.monotonic()
            if not force_refresh and _fresh(cached, now):
                return cached[0]
            if force_refresh and cached is not None and _throttled(now):
                return cached[0]
            keys = await self._fetch_jwks(issuer_cfg)
            self._jwks_cache[issuer_cfg.issuer] = (keys, now)
            if force_refresh:
                self._last_forced_refresh[issuer_cfg.issuer] = now
            return keys

    async def _resolve_key(self, issuer_cfg: _IssuerConfig, kid: str | None) -> PyJWK:
        keys = await self._keys_for_issuer(issuer_cfg)
        if kid is not None and kid in keys:
            return keys[kid]
        # Unrecognised kid: force one refresh in case the issuer rotated keys.
        keys = await self._keys_for_issuer(issuer_cfg, force_refresh=True)
        if kid is not None and kid in keys:
            return keys[kid]
        if kid is None and len(keys) == 1:
            return next(iter(keys.values()))
        raise InvalidTokenError(
            f"No matching JWKS key for kid={kid!r} on issuer {issuer_cfg.issuer!r}"
        )

    @staticmethod
    def _extract_bearer(headers: dict[str, str]) -> str:
        auth_header = headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            raise InvalidTokenError("Missing bearer token")
        token = auth_header[len("bearer ") :].strip()
        if not token:
            raise InvalidTokenError("Missing bearer token")
        return token

    async def validate_headers(self, headers: dict[str, str]) -> Principal:
        """Validate the Authorization bearer JWT. Ignores every other header.

        ``headers`` may legitimately contain caller-supplied ``x-auth-*``
        entries (there is no Envoy to have stripped them) — they are never
        read here.
        """
        token = self._extract_bearer(headers)
        try:
            unverified_claims = jwt.decode(token, options={"verify_signature": False})
            unverified_header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError(f"Malformed bearer token: {exc}") from exc

        issuer = str(unverified_claims.get("iss", ""))
        issuer_cfg = self._issuers.get(issuer)
        if issuer_cfg is None:
            raise InvalidTokenError(f"Untrusted token issuer: {issuer!r}")

        key = await self._resolve_key(issuer_cfg, unverified_header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=[key.algorithm_name],
                issuer=issuer_cfg.issuer,
                audience=issuer_cfg.audiences,
                leeway=self._clock_leeway_seconds,
                options={"require": ["exp"]},
            )
        except jwt.InvalidTokenError as exc:
            raise InvalidTokenError(f"Bearer token failed verification: {exc}") from exc

        user_id = str(claims.get(self._user_id_claim, "") or "")
        if not user_id:
            raise InvalidTokenError(
                f"Verified token is missing required claim {self._user_id_claim!r}"
            )

        roles = _claim_to_roles(_get_claim(claims, self._roles_claim))
        if self._role_mapping:
            roles = [self._role_mapping.get(role, role) for role in roles]

        tenant_id = _get_claim(claims, self._tenant_claim)
        return Principal(
            user_id=user_id,
            email=str(claims.get(self._email_claim, "") or ""),
            tenant_id=str(tenant_id or ""),
            roles=roles,
        )


class JwksIdentityAdapter(EnvoyHeaderIdentityAdapter):
    """Full identity port for hosts without Envoy (JIT provisioning included).

    Verifies the bearer JWT itself (see ``JwksBearerAuthenticationAdapter``),
    then replays *only the claims it just verified* through
    ``EnvoyHeaderIdentityAdapter``'s existing JIT-provisioning, tenant-sync,
    and membership-authority pipeline — never the caller's own headers, so a
    spoofed ``x-auth-*`` header can never reach that pipeline.
    """

    def __init__(
        self,
        *,
        issuers: list[dict[str, Any]] | None = None,
        clock_leeway_seconds: int = DEFAULT_CLOCK_LEEWAY_SECONDS,
        jwks_cache_ttl_seconds: int = DEFAULT_JWKS_CACHE_TTL_SECONDS,
        jwks_timeout_seconds: float = DEFAULT_JWKS_TIMEOUT_SECONDS,
        min_refresh_interval_seconds: float = DEFAULT_MIN_REFRESH_INTERVAL_SECONDS,
        user_id_claim: str = "sub",
        email_claim: str = "email",
        tenant_claim: str = "tenant_id",
        roles_claim: str = "resource_access.volundr.roles",
        **kwargs: object,
    ) -> None:
        self._bearer = JwksBearerAuthenticationAdapter(
            issuers=issuers,
            clock_leeway_seconds=clock_leeway_seconds,
            jwks_cache_ttl_seconds=jwks_cache_ttl_seconds,
            jwks_timeout_seconds=jwks_timeout_seconds,
            min_refresh_interval_seconds=min_refresh_interval_seconds,
            user_id_claim=user_id_claim,
            email_claim=email_claim,
            tenant_claim=tenant_claim,
            roles_claim=roles_claim,
            # Applied exactly once, below, by the inherited Envoy pipeline —
            # see EnvoyHeaderIdentityAdapter.validate_headers.
            role_mapping=None,
        )
        super().__init__(**kwargs)  # type: ignore[arg-type]

    async def validate_headers(self, headers: dict[str, str]) -> Principal:
        principal = await self._bearer.validate_headers(headers)
        return await self.revalidate_verified_principal(principal)

    async def revalidate_verified_principal(self, principal: Principal) -> Principal:
        """Re-run role-mapping and membership derivation for a principal this
        process already verified once — never a second signature check.

        Some callers hold a principal this adapter verified earlier in the
        same request (the session proxy's ownership re-check is the case
        this exists for: it verifies the caller's bearer token once via
        ``extract_principal``, then re-derives *current* membership/roles
        for that already-trusted identity before allowing a session attach).
        There is no fresh token to re-verify at that point — only the
        caller's own already-trusted identity — so this skips straight to
        the inherited Envoy header-claim pipeline instead of demanding a
        bearer token that does not exist there.
        """
        verified_headers = {
            self._user_id_header: principal.user_id,
            self._email_header: principal.email,
            self._tenant_header: principal.tenant_id,
            self._roles_header: ",".join(principal.roles),
        }
        return await super().validate_headers(verified_headers)

    async def validate_token(self, raw_token: str) -> Principal:
        if not raw_token:
            raise InvalidTokenError("Empty token")
        raise InvalidTokenError(
            "JwksIdentityAdapter requires headers, not a raw token. "
            "Ensure the auth dependency calls validate_headers()."
        )
