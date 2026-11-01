"""Which Forge routes a ``forge_session`` credential may call, and how.

This allow-list is shared by the two places that see such a token:

* Forge (Volundr) enforces it fully in ``ForgeSessionAuthMiddleware``, after
  verifying the token and checking the bound session and target ownership.
* The Guild facade applies the route and scope part before forwarding, so a
  refused call is refused at the edge too (Forge still decides).

Each allowed route names the scope it needs and how its ``{session_id}`` relates
to the token's own session: the session itself (``OWN``), any session of the same
owner (``OWNED``), or any *other* session of the same owner (``PEER``). Every
route not listed is closed to session credentials.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from niuu.domain.services.token_scope import (
    FORGE_NOTIFY_SCOPE,
    FORGE_SESSION_LIFECYCLE_SCOPE,
    FORGE_SESSION_MESSAGE_SCOPE,
    FORGE_SESSION_READ_SCOPE,
)

FORGE_PREFIX = "/api/v1/forge"


class Binding(StrEnum):
    """How a route's ``{session_id}`` must relate to the token's own session."""

    NONE = "none"
    OWN = "own"
    OWNED = "owned"
    PEER = "peer"


@dataclass(frozen=True)
class RoutePolicy:
    method: str
    template: str
    scope: str | None
    binding: Binding


#: ``{session_id}`` only matches a UUID, so a sibling route such as
#: ``/sessions/stream`` can never be mistaken for a session read.
_PARAM_PATTERNS = {
    "session_id": r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
}
_ANY_SEGMENT = r"[^/]+"


def _compile(template: str) -> re.Pattern[str]:
    """``/a/{name}/b`` → ``^/a/(?P<name>[^/]+)/b$`` (``session_id`` is a UUID)."""
    parts = re.split(r"(\{\w+\})", template)
    regex = "".join(
        f"(?P<{part[1:-1]}>{_PARAM_PATTERNS.get(part[1:-1], _ANY_SEGMENT)})"
        if part.startswith("{")
        else re.escape(part)
        for part in parts
    )
    return re.compile(f"^{regex}$")


_S = f"{FORGE_PREFIX}/sessions/{{session_id}}"
_READ = FORGE_SESSION_READ_SCOPE

#: Every route a ``forge_session`` token may call. Anything else is 403.
ROUTE_POLICIES: tuple[RoutePolicy, ...] = (
    # Notification feed and read state (the owner's; a session token is never admin).
    RoutePolicy("GET", f"{FORGE_PREFIX}/notifications", _READ, Binding.NONE),
    RoutePolicy("GET", f"{FORGE_PREFIX}/notifications/read-state", _READ, Binding.NONE),
    RoutePolicy("PUT", f"{FORGE_PREFIX}/notifications/read-state", _READ, Binding.NONE),
    RoutePolicy(
        "PUT", f"{FORGE_PREFIX}/notifications/{{notification_id}}/read", _READ, Binding.NONE
    ),
    RoutePolicy("GET", f"{_S}/notifications", _READ, Binding.OWNED),
    # Direct submit only for the token's own session (recorded as source=agent).
    RoutePolicy("POST", f"{_S}/notifications", FORGE_NOTIFY_SCOPE, Binding.OWN),
    # Session reads, owner-scoped.
    RoutePolicy("GET", f"{FORGE_PREFIX}/sessions", _READ, Binding.NONE),
    RoutePolicy("GET", _S, _READ, Binding.OWNED),
    RoutePolicy("GET", f"{_S}/conversation", _READ, Binding.OWNED),
    RoutePolicy("GET", f"{_S}/conversation/turns/{{turn_id}}", _READ, Binding.OWNED),
    RoutePolicy("GET", f"{_S}/log", _READ, Binding.OWNED),
    RoutePolicy("GET", f"{_S}/log/head", _READ, Binding.OWNED),
    RoutePolicy("GET", f"{_S}/transcript", _READ, Binding.OWNED),
    RoutePolicy("GET", f"{_S}/transcript/download", _READ, Binding.OWNED),
    RoutePolicy("GET", f"{_S}/message-deliveries/{{request_id}}", _READ, Binding.OWNED),
    # Steering another session needs the `message` grant; never the session itself.
    RoutePolicy("POST", f"{_S}/messages", FORGE_SESSION_MESSAGE_SCOPE, Binding.PEER),
    # Lifecycle needs the `lifecycle` grant: create, start/resume and stop. No
    # delete or archive, and a session cannot start or stop itself.
    RoutePolicy("POST", f"{FORGE_PREFIX}/sessions", FORGE_SESSION_LIFECYCLE_SCOPE, Binding.NONE),
    RoutePolicy("POST", f"{_S}/start", FORGE_SESSION_LIFECYCLE_SCOPE, Binding.PEER),
    RoutePolicy("POST", f"{_S}/resume", FORGE_SESSION_LIFECYCLE_SCOPE, Binding.PEER),
    RoutePolicy("POST", f"{_S}/stop", FORGE_SESSION_LIFECYCLE_SCOPE, Binding.PEER),
    # Session-bound writes: the session's own record only.
    RoutePolicy("POST", f"{_S}/log", FORGE_NOTIFY_SCOPE, Binding.OWN),
    RoutePolicy("POST", f"{_S}/activity", FORGE_NOTIFY_SCOPE, Binding.OWN),
    RoutePolicy("POST", f"{_S}/usage", FORGE_NOTIFY_SCOPE, Binding.OWN),
    # The Forge-hosted MCP. Each tool re-enters this allow-list as a REST call.
    RoutePolicy("POST", f"{FORGE_PREFIX}/mcp", None, Binding.NONE),
)

_COMPILED: tuple[tuple[RoutePolicy, re.Pattern[str]], ...] = tuple(
    (policy, _compile(policy.template)) for policy in ROUTE_POLICIES
)


def match_policy(method: str, path: str) -> tuple[RoutePolicy, dict[str, str]] | None:
    """The policy for a request, with its path parameters; ``None`` when not allowed."""
    for policy, pattern in _COMPILED:
        if policy.method != method:
            continue
        found = pattern.match(path)
        if found is not None:
            return policy, found.groupdict()
    return None


class SessionPolicyRefusedError(Exception):
    """A session credential may not make this call (route or scope)."""


def admitted_route(
    method: str, path: str, scopes: tuple[str, ...]
) -> tuple[RoutePolicy, dict[str, str]]:
    """The policy and path parameters for an allowed call; raises when refused.

    Covers the route allow-list and the scope; the session binding needs the
    session rows and is Forge's to check.
    """
    matched = match_policy(method, path)
    if matched is None:
        raise SessionPolicyRefusedError(f"Forge session tokens may not call {method} {path}")
    policy, _ = matched
    if policy.scope is not None and policy.scope not in scopes:
        raise SessionPolicyRefusedError(f"Token is missing the required scope: {policy.scope}")
    return matched


def policy_refusal(method: str, path: str, scopes: tuple[str, ...]) -> str | None:
    """Why a session credential with ``scopes`` may not make this call, or ``None``."""
    try:
        admitted_route(method, path, scopes)
    except SessionPolicyRefusedError as refusal:
        return str(refusal)
    return None
