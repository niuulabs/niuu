"""Shared HTTP route-to-room-role policy for a session's broker API.

Widening the session proxy's attach guard to any active participant (not
just owner/admin) means "attach" alone is no longer enough authority to call
every route under a session's ``/api/*`` — a viewer who can attach must not
also be able to POST /api/services (host code execution in local mode) or
resolve a workflow gate.

This table is enforced in TWO places that must never drift from each other:
the session proxy (``niuu.session_proxy``, which forwards browser HTTP
traffic to the broker) and the broker itself (``skuld.broker_api``,
defense-in-depth against anything that reaches it directly, e.g. through the
proxy's generic passthrough with only the coarse attach guard already
having run). Both call ``required_role_for_route`` from here so the two
enforcement points share one source of truth.
"""

from __future__ import annotations

import re

# The single name for the session-proxy-verified room-role header. Every
# other module that needs it (skuld.broker_api, volundr's rest.py,
# niuu.session_proxy) imports this constant rather than repeating the
# literal, so the four call sites can never drift from each other.
ROOM_ROLE_HEADER = "x-niuu-room-role"

ROOM_ROLE_RANK = {"viewer": 0, "approver": 1, "owner": 2}

# Ordered allowlists: (HTTP method, path-regex matched against the request
# path relative to a session's /api/, e.g. "conversation/history" or
# "workflow/gates/g1/resolve"). This is a STRICT ALLOWLIST, not a denylist —
# required_role_for_route defaults to "owner" for anything that matches
# neither list, so a new broker route that forgets to appear here is
# owner-only by construction, never accidentally open to a viewer.
#
# POST /api/message (session-to-session dispatch, not room chat) is
# DELIBERATELY absent: it lets the caller name an arbitrary body.session_id
# and posts into THAT session using the broker's own credentials. Even
# approver is too much authority for that; it stays owner-only via the
# default below. POST /api/room/message and /api/room/direct ARE the room
# chat primitives and stay viewer-level, but the route handlers themselves
# must not trust a sub-owner caller's participant_id/source — see
# broker_api.py's send_room_message/send_directed_room_message.
_VIEWER_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GET", re.compile(r"^conversation/history$")),
    ("GET", re.compile(r"^conversation/tool-result/[^/]+$")),
    ("GET", re.compile(r"^conversation/tool-result/[^/]+/files/[^/]+$")),
    ("GET", re.compile(r"^capabilities$")),
    ("GET", re.compile(r"^workflow/gates$")),
    ("GET", re.compile(r"^room/participants$")),
    ("GET", re.compile(r"^help/requests$")),
    ("GET", re.compile(r"^files/presented/[^/]+$")),
    ("POST", re.compile(r"^room/message$")),
    ("POST", re.compile(r"^room/direct$")),
)
# Approver gets everything viewer gets, plus these.
_APPROVER_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^workflow/gates/[^/]+/resolve$")),
    # Answering a peer's help request is an operator decision, same tier as
    # the WS ask_user_answer/permission_response approval wait.
    ("POST", re.compile(r"^help/requests/[^/]+/answer$")),
)


def required_role_for_route(method: str, path: str) -> str:
    """Return the minimum room role ("viewer"/"approver"/"owner") for a route.

    *path* is relative to a session's ``/api/`` root, with no leading slash
    (matching what the session proxy strips before forwarding, and what a
    broker request's path looks like once its own ``/api/`` prefix is cut).
    Unmatched routes require "owner" — the fail-closed default for anything
    not explicitly reviewed and allowlisted here.
    """
    normalized = path.strip("/")
    upper_method = method.upper()
    for allowed_method, pattern in _VIEWER_ROUTES:
        if allowed_method == upper_method and pattern.match(normalized):
            return "viewer"
    for allowed_method, pattern in _APPROVER_ROUTES:
        if allowed_method == upper_method and pattern.match(normalized):
            return "approver"
    return "owner"
