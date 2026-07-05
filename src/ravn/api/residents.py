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
import re
from typing import Any

import httpx
from fastapi import Request

from ravn.ports.resident_discovery import ResidentDiscoveryPort, StandaloneResident

logger = logging.getLogger(__name__)

_DEFAULT_PLATFORM_API_URL = "http://localhost:8080"  # matches PlatformToolsConfig.base_url
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

# Session ids accepted for downstream forge lookup.
_RAVN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

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

# Forge session status → ravn Session status (web session vocabulary:
# running | idle | stopped | failed).
_SESSION_STATUS_MAP = {
    "created": "idle",
    "starting": "idle",
    "provisioning": "idle",
    "running": "running",
    "stopping": "stopped",
    "stopped": "stopped",
    "failed": "failed",
    "archived": "stopped",
}

# The workload types that ARE ravn agents you can chat with / steer (each runs
# a Skuld room). Plain coding sessions are not "ravn sessions".
_RAVN_SESSION_WORKLOADS = frozenset({"resident", "ravn_flock"})

# StandaloneResident status (raven fleet vocabulary) → ravn Session status
# (web session vocabulary: running | idle | stopped | failed).
_STANDALONE_SESSION_STATUS_MAP = {
    "active": "running",
    "idle": "idle",
    "suspended": "stopped",
    "failed": "failed",
    "completed": "stopped",
}


def _sanitize_for_log(value: str) -> str:
    """Remove line-break/control characters to prevent log-forging."""
    return value.replace("\r", "").replace("\n", "")


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
    """Discovers resident ravns from the Volundr Forge sessions API.

    Optionally merges in standalone residents (helm-deployed, outside Forge)
    reported by a :class:`ResidentDiscoveryPort`. Forge is the primary source:
    a discovery failure degrades to forge-only results, while forge failures
    still propagate loudly.
    """

    def __init__(
        self,
        *,
        base_url: str = "",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        discovery: ResidentDiscoveryPort | None = None,
    ) -> None:
        # Configuration-driven: create_app threads gateway.platform.base_url
        # from the ravn Settings here (config file or standard RAVN_ env
        # overrides) — no bespoke environment lookups.
        self._base_url = (base_url or _DEFAULT_PLATFORM_API_URL).rstrip("/")
        self._timeout = timeout_seconds
        self._discovery = discovery
        # One pooled client for the directory's lifetime — /ravens and
        # /settings hit this per request, so a fresh client (new TCP+TLS)
        # per call is pure setup waste.
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

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
        ravens = [
            self._to_raven(session)
            for session in sessions
            if isinstance(session, dict)
            and session.get("workload_type") == "resident"
            and session.get("resident")
        ]
        forge_ids = {raven["id"] for raven in ravens}
        ravens.extend(
            self._standalone_to_raven(resident)
            for resident in await self._discover_standalone()
            if resident.id not in forge_ids
        )
        return ravens

    async def list_sessions(
        self,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return the caller's live ravn sessions (resident + flock rooms).

        These are the real running sessions you can open and chat with, the
        Ravn-side equivalent of the Volundr live session list — each carries a
        ``chat_endpoint`` (its Skuld room) so the UI can reuse the shared chat.
        """
        sessions = await self._get_json(
            "/api/v1/forge/sessions",
            auth_headers,
            auth_params,
        )
        if not isinstance(sessions, list):
            raise RuntimeError(
                f"Forge sessions API returned unexpected payload: {type(sessions).__name__}"
            )
        ravn_sessions = [
            self._to_session(session)
            for session in sessions
            if isinstance(session, dict) and session.get("workload_type") in _RAVN_SESSION_WORKLOADS
        ]
        forge_ids = {session["id"] for session in ravn_sessions}
        ravn_sessions.extend(
            self._standalone_to_session(resident)
            for resident in await self._discover_standalone()
            if resident.id not in forge_ids
        )
        return ravn_sessions

    async def get_session(
        self,
        session_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        """Return one live ravn session by id, or None."""
        if not _RAVN_ID_RE.fullmatch(session_id):
            return None
        try:
            session = await self._get_json(
                f"/api/v1/forge/sessions/{session_id}",
                auth_headers,
                auth_params,
            )
        except httpx.HTTPStatusError as exc:
            if 400 <= exc.response.status_code < 500:
                return await self._standalone_session(session_id)
            raise
        if (
            not isinstance(session, dict)
            or session.get("workload_type") not in _RAVN_SESSION_WORKLOADS
        ):
            return await self._standalone_session(session_id)
        return self._to_session(session)

    @staticmethod
    def _to_session(session: dict[str, Any]) -> dict[str, Any]:
        resident = session.get("resident") or {}
        # ravnId is required by the web schema; prefer the resident peer id, fall
        # back to the session id so a flock session still has a stable owner ref.
        ravn_id = resident.get("peer_id") or session.get("id")
        return {
            "id": session.get("id"),
            "ravn_id": ravn_id,
            "persona_name": resident.get("persona") or session.get("name") or "",
            "status": _SESSION_STATUS_MAP.get(str(session.get("status") or ""), "idle"),
            "model": session.get("model") or "",
            "created_at": session.get("created_at"),
            "chat_endpoint": session.get("chat_endpoint"),
            "title": resident.get("name") or session.get("name") or "",
        }

    async def get_raven(
        self,
        ravn_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        """Return one resident raven by its session id, or None."""
        if not _RAVN_ID_RE.fullmatch(ravn_id):
            return None
        try:
            session = await self._get_json(
                f"/api/v1/forge/sessions/{ravn_id}",
                auth_headers,
                auth_params,
            )
        except httpx.HTTPStatusError as exc:
            # Any client-side status (404 not found, 422 non-UUID id, 401/403
            # not permitted) means "no such resident for this caller" — a clean
            # None, not a 500. Only server errors propagate. Log auth failures
            # so a bad-credential misconfig isn't indistinguishable from a
            # genuinely-missing resident.
            status = exc.response.status_code
            if status in (401, 403):
                logger.warning(
                    "resident get_raven: forge returned %s for %s (auth?) — reporting None",
                    status,
                    _sanitize_for_log(ravn_id),
                )
            if 400 <= status < 500:
                return await self._standalone_raven(ravn_id)
            raise
        if (
            not isinstance(session, dict)
            or session.get("workload_type") != "resident"
            or not session.get("resident")
        ):
            return await self._standalone_raven(ravn_id)
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

    async def _discover_standalone(self) -> list[StandaloneResident]:
        """Return discovered standalone residents; never break forge results."""
        if self._discovery is None:
            return []
        try:
            return await self._discovery.list_residents()
        except Exception:
            logger.warning(
                "standalone resident discovery failed; serving forge results only",
                exc_info=True,
            )
            return []

    async def _standalone_raven(self, ravn_id: str) -> dict[str, Any] | None:
        for resident in await self._discover_standalone():
            if resident.id == ravn_id:
                return self._standalone_to_raven(resident)
        return None

    async def _standalone_session(self, session_id: str) -> dict[str, Any] | None:
        for resident in await self._discover_standalone():
            if resident.id == session_id:
                return self._standalone_to_session(resident)
        return None

    @staticmethod
    def _standalone_to_raven(resident: StandaloneResident) -> dict[str, Any]:
        return {
            "id": resident.id,
            "persona_name": resident.persona_name,
            "resident_name": resident.resident_name,
            "peer_id": "",
            "kind": "resident",
            "status": resident.status,
            "model": resident.model,
            "created_at": resident.created_at,
            "updated_at": resident.updated_at,
            "chat_endpoint": resident.chat_endpoint,
            "session_id": resident.id,
            "location": resident.location,
            "deployment": "standalone",
        }

    @staticmethod
    def _standalone_to_session(resident: StandaloneResident) -> dict[str, Any]:
        return {
            "id": resident.id,
            "ravn_id": resident.id,
            "persona_name": resident.persona_name,
            "status": _STANDALONE_SESSION_STATUS_MAP.get(resident.status, "idle"),
            "model": resident.model,
            "created_at": resident.created_at,
            "chat_endpoint": resident.chat_endpoint,
            "title": resident.resident_name,
        }

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
