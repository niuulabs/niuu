"""Agent authentication for Bifröst.

Defines the core identity types shared across all authentication modes:

- ``AuthMode``         — enum of supported authentication modes.
- ``AgentIdentity``    — caller identity attached to every tracked request.
- ``_read_attribution_headers`` — helper used by auth adapters.
- ``read_agent_id`` / ``read_tenant_id`` — the same, for caller identity.

Authentication logic lives in the adapter layer:
  ``bifrost.adapters.auth.open``  — Open / trust-all mode
  ``bifrost.adapters.auth.pat``   — PAT Bearer-JWT mode
  ``bifrost.adapters.auth.mesh``  — Service-mesh / Envoy mTLS mode
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from fastapi import Request


class AuthMode(StrEnum):
    """Authentication mode for the Bifröst gateway."""

    OPEN = "open"
    """No authentication — headers are trusted verbatim."""

    PAT = "pat"
    """Bearer-token Personal Access Token (HS256 JWT)."""

    MESH = "mesh"
    """Service-mesh / Envoy injected identity headers."""

    OIDC = "oidc"
    """In-process JWT verification via JWKS — for a host without Envoy.

    Set automatically by the CLI host when ``host_auth.mode: oidc``
    (``cli.commands.platform._resolve_local_pod_manager_env``); every
    caller must present a bearer token signature-verified against the
    configured OIDC issuer(s), the same verification
    ``identity.adapters.jwks.JwksBearerAuthenticationAdapter`` performs for
    every other co-hosted service. ``X-Agent-Id`` header spoofing is not
    accepted in this mode.
    """


@dataclass
class AgentIdentity:
    """Caller identity attached to every tracked request."""

    agent_id: str = "anonymous"
    tenant_id: str = "default"
    session_id: str = ""
    saga_id: str = ""
    #: Roles asserted by the verified credential, when the auth mode can
    #: carry them: oidc (the token's role claim, via JwksBearerAuthentication
    #: Adapter/Principal.roles) and pat (an optional 'roles' claim — not
    #: every PAT issuer sets one; niuu.adapters.memory_token_issuer never
    #: does). Empty for 'mesh'/'open', which have no per-caller role concept.
    #: See admin_reload_keys in bifrost.inbound.routes for the one place
    #: this is currently enforced.
    roles: tuple[str, ...] = ()


#: Attribution headers, canonical name first.
#:
#: Ravn's Bifröst adapter has always sent the `X-Ravn-*` spelling, and this
#: module has always read the plain one, so every request in the estate was
#: attributed to `anonymous` — the usage store has the columns for a caller and
#: never saw one. Both are accepted rather than picking a winner: the plain
#: names are what a non-Ravn caller would reach for, and the prefixed ones are
#: what every deployed Ravn already sends.
_AGENT_ID_HEADERS = ("x-agent-id", "x-ravn-agent-id")
_TENANT_ID_HEADERS = ("x-tenant-id", "x-ravn-tenant-id")
_SESSION_ID_HEADERS = ("x-session-id", "x-ravn-session-id")
_SAGA_ID_HEADERS = ("x-saga-id", "x-ravn-saga-id")


def _first_header(request: Request, names: tuple[str, ...], default: str = "") -> str:
    for name in names:
        value = request.headers.get(name, "").strip()
        if value:
            return value
    return default


def read_agent_id(request: Request) -> str:
    """The calling agent, or `anonymous` when nothing identified itself."""
    return _first_header(request, _AGENT_ID_HEADERS, "anonymous")


def read_tenant_id(request: Request) -> str:
    return _first_header(request, _TENANT_ID_HEADERS, "default")


def _read_attribution_headers(request: Request) -> tuple[str, str]:
    """Return (session_id, saga_id) from standard attribution headers."""
    return (
        _first_header(request, _SESSION_ID_HEADERS),
        _first_header(request, _SAGA_ID_HEADERS),
    )
