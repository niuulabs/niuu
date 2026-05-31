"""Auth adapter package — factory for the configured authentication mode."""

from __future__ import annotations

from bifrost.auth import AuthMode
from bifrost.ports.auth import AuthPort


def build_auth_adapter(mode: AuthMode, pat_secret: str = "") -> AuthPort:
    """Instantiate the ``AuthPort`` adapter for *mode*.

    Args:
        mode:       Authentication mode (open / pat / mesh).
        pat_secret: HS256 signing secret; required when ``mode`` is ``pat``.

    Returns:
        A configured ``AuthPort`` implementation.
    """
    match mode:
        case AuthMode.PAT:
            from bifrost.adapters.auth.pat import PATAuthAdapter

            return PATAuthAdapter(secret=pat_secret)
        case AuthMode.MESH:
            from bifrost.adapters.auth.mesh import MeshAuthAdapter

            return MeshAuthAdapter()
        case _:
            from bifrost.adapters.auth.open import OpenAuthAdapter

            return OpenAuthAdapter()
    raise AssertionError("Unreachable build_auth_adapter fallthrough")
