"""Real resident discovery for the Ravn HTTP API.

Ravens in the fleet UI are the operator's RESIDENT sessions — long-lived
flock-of-one chat agents provisioned by Volundr (``workload_type ==
"resident"``). This module discovers them live from the Forge sessions API
instead of serving seed data.

Module boundaries: ravn must not import volundr, so discovery goes over
HTTP with the caller's auth forwarded verbatim. Ownership scoping is
therefore enforced by Volundr — a caller only ever sees their own
residents.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import Request

logger = logging.getLogger(__name__)

_DEFAULT_PLATFORM_API_URL = "http://127.0.0.1:8080"
_DEFAULT_TIMEOUT_SECONDS = 5.0

# Headers that carry caller identity (Envoy-injected or bearer); forwarded
# verbatim so Volundr resolves the same principal this request carries.
_AUTH_HEADERS = (
    "authorization",
    "x-auth-user-id",
    "x-auth-email",
    "x-auth-tenant",
    "x-auth-roles",
)
# Developer-identity query params (mirrors volundr's extract_principal).
_AUTH_QUERY_PARAMS = ("devUserId", "devEmail", "devTenantId", "devRoles")

# Forge session status/activity → RavnStatus (web fleet vocabulary).
_STATUS_MAP = {
    "created": "idle",
    "starting": "idle",
    "provisioning": "idle",
    "running": "active",
    "stopping": "suspended",
    "stopped": "suspended",
    "failed": "failed",
    "archived": "completed",
}


def forward_auth(request: Request) -> tuple[dict[str, str], dict[str, str]]:
    """Extract forwardable auth headers and dev-identity query params."""
    headers = {
        name: value for name in _AUTH_HEADERS if (value := request.headers.get(name, "").strip())
    }
    params = {
        name: value
        for name in _AUTH_QUERY_PARAMS
        if (value := request.query_params.get(name, "").strip())
    }
    return headers, params


class ResidentDirectory:
    """Discovers resident ravns from the Volundr Forge sessions API."""

    def __init__(
        self,
        *,
        base_url: str = "",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = (
            base_url
            or os.environ.get("RAVN_PLATFORM_API_URL", "")
            or os.environ.get("NIUU_PLATFORM_API_URL", "")
            or _DEFAULT_PLATFORM_API_URL
        ).rstrip("/")
        self._timeout = timeout_seconds

    async def list_ravens(
        self,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return every resident session visible to the caller as a raven."""
        sessions = await self._get_json(
            "/api/v1/forge/sessions",
            auth_headers,
            auth_params,
        )
        if not isinstance(sessions, list):
            raise RuntimeError(
                f"Forge sessions API returned unexpected payload: {type(sessions).__name__}"
            )
        return [
            self._to_raven(session)
            for session in sessions
            if isinstance(session, dict)
            and session.get("workload_type") == "resident"
            and session.get("resident")
        ]

    async def get_raven(
        self,
        ravn_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        """Return one resident raven by its session id, or None."""
        try:
            session = await self._get_json(
                f"/api/v1/forge/sessions/{ravn_id}",
                auth_headers,
                auth_params,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        if (
            not isinstance(session, dict)
            or session.get("workload_type") != "resident"
            or not session.get("resident")
        ):
            return None
        return self._to_raven(session)

    async def _get_json(
        self,
        path: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> Any:
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, headers=auth_headers, params=auth_params)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _to_raven(session: dict[str, Any]) -> dict[str, Any]:
        resident = session.get("resident") or {}
        status = _STATUS_MAP.get(str(session.get("status") or ""), "idle")
        # A running resident that idles between messages is still 'active'
        # fleet-wise — the session status, not activity, drives the state.
        return {
            "id": session.get("id"),
            "persona_name": resident.get("persona") or "",
            "resident_name": resident.get("name") or "",
            "peer_id": resident.get("peer_id") or "",
            "kind": "resident",
            "status": status,
            "model": session.get("model") or "",
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
            "chat_endpoint": session.get("chat_endpoint"),
            "session_id": session.get("id"),
            "location": session.get("pod_name") or "",
            "deployment": "resident",
        }
