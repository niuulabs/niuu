"""Resident discovery and live-session proxying for the Ravn HTTP API.

Ravens in the fleet UI are STANDALONE residents — long-lived chat agents
deployed as infrastructure via the skuld chart's resident mode and found
through a :class:`ResidentDiscoveryPort` (e.g. the ``niuu.world/kind=resident``
cluster label). Nothing provisions residents as Forge sessions anymore.

The Forge sessions API is still proxied for LIVE RAVN SESSIONS: ravn_flock
workflow sessions remain ordinary Forge sessions with a Skuld room, and the
session endpoints merge those with the discovered residents.

Module boundaries: ravn must not import volundr, so the session proxy goes
over HTTP with the caller's auth forwarded verbatim. Ownership scoping is
therefore enforced by Volundr — a caller only ever sees their own sessions.
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
_LIVE_SESSION_STATUSES = frozenset({"running", "idle"})

# The workload types that ARE ravn agents you can chat with / steer (each runs
# a Skuld room). Plain coding sessions are not "ravn sessions".
_RAVN_SESSION_WORKLOADS = frozenset({"ravn_flock"})

# StandaloneResident status (raven fleet vocabulary) → ravn Session status
# (web session vocabulary: running | idle | stopped | failed).
_STANDALONE_SESSION_STATUS_MAP = {
    "active": "running",
    "idle": "idle",
    "suspended": "stopped",
    "failed": "failed",
    "completed": "stopped",
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
    """Serves the ravn fleet: discovered residents plus live Forge sessions.

    Ravens come exclusively from a :class:`ResidentDiscoveryPort` (standalone
    residents deployed via the skuld chart) — discovery failures there
    propagate loudly. Sessions proxy the Forge API (ravn_flock rooms) and
    merge in the discovered residents; on the session path a discovery
    failure degrades to forge-only results, while forge failures propagate.
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

    async def list_ravens(self) -> list[dict[str, Any]]:
        """Return every discovered standalone resident as a raven.

        Discovery is the ONLY source for ravens now, so failures propagate —
        a broken discovery must not masquerade as an empty fleet.
        """
        if self._discovery is None:
            return []
        return [
            self._standalone_to_raven(resident)
            for resident in await self._discovery.list_residents()
        ]

    async def list_sessions(
        self,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return the caller's live ravn sessions (flock rooms + residents).

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
        ravn_sessions = []
        for session in sessions:
            if not isinstance(session, dict):
                continue
            if session.get("workload_type") not in _RAVN_SESSION_WORKLOADS:
                continue
            mapped = self._to_session(session)
            if _is_live_session(mapped):
                ravn_sessions.append(mapped)
        forge_ids = {session["id"] for session in ravn_sessions}
        for resident in await self._discover_standalone():
            if resident.id in forge_ids:
                continue
            mapped = self._standalone_to_session(resident)
            if _is_live_session(mapped):
                ravn_sessions.append(mapped)
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
        mapped = self._to_session(session)
        if not _is_live_session(mapped):
            return None
        return mapped

    async def stop_session(
        self,
        session_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        """Stop one Forge-backed Ravn session, returning its live response.

        Standalone residents are discovery-managed and do not expose a Forge
        lifecycle endpoint. A downstream 4xx therefore means this directory
        cannot stop the requested session and is reported to the caller rather
        than being converted into a synthetic stopped state.
        """
        if not _RAVN_ID_RE.fullmatch(session_id):
            return None
        try:
            result = await self._request_json(
                "POST",
                f"/api/v1/forge/sessions/{session_id}/stop",
                auth_headers,
                auth_params,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        if not isinstance(result, dict):
            raise RuntimeError(
                f"Forge stop API returned unexpected payload: {type(result).__name__}"
            )
        return result

    @staticmethod
    def _to_session(session: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": session.get("id"),
            # ravnId is required by the web schema; the session id gives a
            # flock session a stable owner ref.
            "ravn_id": session.get("id"),
            "persona_name": session.get("name") or "",
            "status": _SESSION_STATUS_MAP.get(str(session.get("status") or ""), "idle"),
            "model": session.get("model") or "",
            "created_at": session.get("created_at"),
            "chat_endpoint": session.get("chat_endpoint"),
            "title": session.get("name") or "",
        }

    async def get_raven(self, ravn_id: str) -> dict[str, Any] | None:
        """Return one discovered standalone resident by its id, or None."""
        if self._discovery is None:
            return None
        for resident in await self._discovery.list_residents():
            if resident.id == ravn_id:
                return self._standalone_to_raven(resident)
        return None

    async def _get_json(
        self,
        path: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> Any:
        return await self._request_json("GET", path, auth_headers, auth_params)

    async def _request_json(
        self,
        method: str,
        path: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> Any:
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method,
                url,
                headers=auth_headers,
                params=auth_params,
            )
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

    async def _standalone_session(self, session_id: str) -> dict[str, Any] | None:
        for resident in await self._discover_standalone():
            if resident.id == session_id:
                mapped = self._standalone_to_session(resident)
                if _is_live_session(mapped):
                    return mapped
                return None
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


def _is_live_session(session: dict[str, Any]) -> bool:
    """Return True for sessions worth showing in the live Ravn control room."""
    return str(session.get("status") or "") in _LIVE_SESSION_STATUSES
