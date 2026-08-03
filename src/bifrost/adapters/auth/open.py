"""Open (no-auth) authentication adapter.

All callers are trusted; identity is read verbatim from request headers.
Suitable for Pi / local-network deployments or services behind a fully
trusted proxy.
"""

from __future__ import annotations

from fastapi import Request

from bifrost.auth import (
    AgentIdentity,
    _read_attribution_headers,
    read_agent_id,
    read_tenant_id,
)
from bifrost.ports.auth import AuthPort


class OpenAuthAdapter(AuthPort):
    """Trust all callers and read identity from plain request headers.

    No token validation is performed.  This mode is appropriate when
    Bifröst runs in a fully trusted environment (localhost, closed LAN,
    or behind a network-level access control layer).
    """

    def extract(self, request: Request) -> AgentIdentity:
        session_id, saga_id = _read_attribution_headers(request)
        return AgentIdentity(
            agent_id=read_agent_id(request),
            tenant_id=read_tenant_id(request),
            session_id=session_id,
            saga_id=saga_id,
        )
