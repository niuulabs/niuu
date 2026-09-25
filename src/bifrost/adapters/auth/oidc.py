"""OIDC (in-process JWKS) authentication adapter.

For a host without Envoy in front of it (``host_auth.mode: oidc`` — see
``cli.config.AuthConfig``), nothing has verified a bearer JWT's signature
before it reaches Bifröst. This adapter closes that gap the same way every
other co-hosted service does: it delegates signature and claim verification
to ``identity.adapters.jwks.JwksBearerAuthenticationAdapter``, the shared
in-process JWKS verifier, rather than re-implementing JWT verification here.

Only a signature-verified bearer token can assert identity in this mode —
``X-Agent-Id`` / ``X-Tenant-Id`` application headers are never read.
"""

from __future__ import annotations

import jwt
from fastapi import HTTPException, Request

from bifrost.auth import AgentIdentity, _read_attribution_headers
from bifrost.ports.auth import AuthPort
from identity.adapters.jwks import JwksBearerAuthenticationAdapter
from niuu.domain.services.pat_validator import PATValidator
from niuu.domain.services.token_scope import token_requires_scope_check
from niuu.ports.identity import HeaderAuthenticationPort, InvalidTokenError


class OidcAuthAdapter(AuthPort):
    """Verify a bearer JWT via JWKS and derive caller identity from its claims.

    Args:
        bearer: A ``HeaderAuthenticationPort`` that verifies the
            ``Authorization`` header's bearer token signature against the
            configured OIDC issuer(s) — in production this is always
            ``identity.adapters.jwks.JwksBearerAuthenticationAdapter``,
            constructed by ``bifrost.adapters.auth.build_auth_adapter`` from
            ``BifrostConfig.oidc_kwargs`` (threaded from the CLI host's
            ``host_auth.oidc`` — see ``cli.config._oidc_adapter_kwargs``).
        revocation_validator: Optional — applied when the verified bearer
            happens to be a PAT (an IDP-backed ``TokenIssuer`` PAT shares the
            same issuer/JWKS as a regular token). ``PATValidator.is_valid``
            already no-ops for a non-PAT token (its own ``type`` claim check),
            so this is safe to pass unconditionally whenever
            ``bifrost.pat_revocation`` is configured — see
            ``bifrost.app._build_pat_revocation_validator``. Unlike ``pat``
            mode, oidc has no requirement to configure this.
    """

    def __init__(
        self,
        bearer: HeaderAuthenticationPort,
        *,
        revocation_validator: PATValidator | None = None,
    ) -> None:
        self._bearer = bearer
        self._revocation_validator = revocation_validator

    async def extract(self, request: Request) -> AgentIdentity:
        headers = dict(request.headers)
        try:
            principal = await self._bearer.validate_headers(headers)
        except InvalidTokenError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

        if not principal.tenant_id:
            # A tenant claim absent from a verified token is not "use the
            # shared default tenant" — that would pool every tenant-less
            # caller into one quota/usage bucket, letting one caller exhaust
            # or read another's budget. Refuse instead; fix the issuer's
            # token claims (tenant_claim in auth.oidc, or the IDP's own
            # client mapper) to include a real tenant.
            raise HTTPException(
                status_code=403,
                detail="Verified token has no tenant claim; refusing to pool it "
                "into a shared default tenant",
            )

        # The signature is already verified above — this decode only reads
        # claims Principal doesn't carry (token_use / scope), the same
        # unverified-decode-after-verification posture PATAuthAdapter and
        # niuu.domain.services.token_scope use throughout the platform.
        # Reuses the exact extraction JwksBearerAuthenticationAdapter used
        # to verify the signature, rather than re-deriving a (possibly
        # differently-whitespaced) substring of the header here.
        token = JwksBearerAuthenticationAdapter._extract_bearer(headers)
        claims = jwt.decode(token, options={"verify_signature": False})
        if token_requires_scope_check(claims):
            # A scoped workload token or scoped PAT is only valid at its
            # named entry points (see niuu.domain.services.token_scope);
            # Bifröst model access is not one of them, mirroring
            # PATAuthAdapter's identical check.
            raise HTTPException(status_code=403, detail="Credential does not grant model access")

        if self._revocation_validator is not None and not await self._revocation_validator.is_valid(
            token
        ):
            raise HTTPException(status_code=401, detail="Token has been revoked")

        session_id, saga_id = _read_attribution_headers(request)
        return AgentIdentity(
            agent_id=principal.user_id,
            tenant_id=principal.tenant_id,
            session_id=session_id,
            saga_id=saga_id,
            roles=tuple(principal.roles),
        )
