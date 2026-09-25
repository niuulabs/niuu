"""Auth adapter package — factory for the configured authentication mode."""

from __future__ import annotations

from typing import Any

from bifrost.auth import AuthMode
from bifrost.ports.auth import AuthPort
from niuu.domain.services.pat_validator import PATValidator


def build_auth_adapter(
    mode: AuthMode,
    pat_secret: str = "",
    *,
    oidc_kwargs: dict[str, Any] | None = None,
    pat_revocation_validator: PATValidator | None = None,
) -> AuthPort:
    """Instantiate the ``AuthPort`` adapter for *mode*.

    Args:
        mode:       Authentication mode (open / pat / mesh / oidc).
        pat_secret: HS256 signing secret; required when ``mode`` is ``pat``.
        oidc_kwargs: Kwargs for ``identity.adapters.jwks.JwksBearerAuthenticationAdapter``
            (issuers, clock leeway, ...); required when ``mode`` is ``oidc``.
        pat_revocation_validator: Applied to PATs accepted in ``pat`` mode — see
            ``bifrost.adapters.auth.pat.PATAuthAdapter`` — and, optionally, to
            a verified oidc bearer that happens to be a PAT — see
            ``bifrost.adapters.auth.oidc.OidcAuthAdapter``. ``None`` means no
            revocation check runs; for ``pat`` mode that is only reachable
            through an explicit ``pat_revocation.enabled: false``
            (``BifrostConfig._pat_mode_requires_revocation_decision`` refuses
            to start otherwise) — see ``bifrost.app._build_pat_revocation_validator``,
            which is what actually constructs the value passed here.

    Raises:
        ValueError: *mode* is not a known ``AuthMode`` — never silently
            treated as ``open``. A config typo must fail loudly, not run the
            gateway wide open under a misspelled mode string.

    Returns:
        A configured ``AuthPort`` implementation.
    """
    match mode:
        case AuthMode.PAT:
            from bifrost.adapters.auth.pat import PATAuthAdapter

            return PATAuthAdapter(secret=pat_secret, revocation_validator=pat_revocation_validator)
        case AuthMode.MESH:
            from bifrost.adapters.auth.mesh import MeshAuthAdapter

            return MeshAuthAdapter()
        case AuthMode.OIDC:
            from bifrost.adapters.auth.oidc import OidcAuthAdapter
            from identity.adapters.jwks import JwksBearerAuthenticationAdapter

            return OidcAuthAdapter(
                JwksBearerAuthenticationAdapter(**(oidc_kwargs or {})),
                revocation_validator=pat_revocation_validator,
            )
        case AuthMode.OPEN:
            from bifrost.adapters.auth.open import OpenAuthAdapter

            return OpenAuthAdapter()
        case _:
            raise ValueError(
                f"Unknown bifrost auth_mode: {mode!r} (expected one of "
                f"{sorted(AuthMode)!r}). This must never silently fall back to "
                "'open' — fix the configured auth_mode."
            )
