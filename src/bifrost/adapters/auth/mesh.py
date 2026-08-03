"""Service-mesh (Envoy mTLS) authentication adapter.

In service-mesh mode Envoy has already authenticated the caller via mutual
TLS before the request reaches Bifröst.  The ``X-Forwarded-Client-Cert``
(XFCC) header carries the peer certificate information, including its
SPIFFE URI SAN (e.g. ``spiffe://cluster.local/ns/default/sa/volundr``).

Identity extraction strategy:

1. If the XFCC header is present and contains a ``URI=`` field, the last
   path segment of the SPIFFE URI is used as the ``agent_id`` (e.g.
   ``volundr`` from ``spiffe://…/sa/volundr``).
2. If XFCC is absent or unparseable, fall back to the plain
   ``X-Agent-Id`` header (set by the calling service).
3. ``X-Tenant-Id`` is always read from the application-level header.

This adapter must only be used when Envoy / the service mesh is trusted
to inject accurate XFCC headers.  Never use it without a sidecar proxy.
"""

from __future__ import annotations

import re

from fastapi import Request

from bifrost.auth import (
    AgentIdentity,
    _read_attribution_headers,
    read_agent_id,
    read_tenant_id,
)
from bifrost.ports.auth import AuthPort

# Matches the URI field in an Envoy XFCC header segment, e.g.:
#   By=spiffe://…;Hash=…;URI=spiffe://cluster.local/ns/default/sa/volundr;…
_XFCC_URI_RE = re.compile(r"URI=([^,;]+)", re.IGNORECASE)


def _parse_spiffe_workload(xfcc: str) -> str | None:
    """Extract the workload name from an Envoy XFCC header value.

    Returns the last path segment of the SPIFFE URI (the Kubernetes
    service-account name) or ``None`` when the header cannot be parsed.
    """
    match = _XFCC_URI_RE.search(xfcc)
    if not match:
        return None
    uri = match.group(1).rstrip("/")
    # Last segment: spiffe://cluster.local/ns/foo/sa/<workload>
    workload = uri.rsplit("/", 1)[-1]
    return workload or None


class MeshAuthAdapter(AuthPort):
    """Trust Envoy-injected mTLS / XFCC headers for caller identity.

    Identity is derived from the ``X-Forwarded-Client-Cert`` header set
    by Envoy after successful mTLS verification.  Application-level
    ``X-Agent-Id`` / ``X-Tenant-Id`` headers are used as fallbacks when
    XFCC is absent.
    """

    def extract(self, request: Request) -> AgentIdentity:
        session_id, saga_id = _read_attribution_headers(request)
        xfcc = request.headers.get("x-forwarded-client-cert", "")
        spiffe_agent = _parse_spiffe_workload(xfcc) if xfcc else None

        return AgentIdentity(
            agent_id=spiffe_agent or read_agent_id(request),
            tenant_id=read_tenant_id(request),
            session_id=session_id,
            saga_id=saga_id,
        )
