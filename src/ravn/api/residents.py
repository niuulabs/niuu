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

from fastapi import Request

from niuu.domain.models import InstanceVisibility, Principal
from ravn.ports.platform_runtime import PlatformRuntimePort
from ravn.ports.resident_discovery import ResidentDiscoveryPort, StandaloneResident

logger = logging.getLogger(__name__)

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

_MANAGED_SESSION_STATUS_MAP = {
    "pending": "idle",
    "deploying": "idle",
    "active": "running",
    "suspended": "stopped",
    "failed": "failed",
    "deleting": "stopped",
}

_MANAGED_RAVEN_STATUS_MAP = {
    "pending": "idle",
    "deploying": "idle",
    "active": "active",
    "suspended": "suspended",
    "failed": "failed",
    "deleting": "completed",
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

    Durable Volundr records are authoritative for managed residents. A
    :class:`ResidentDiscoveryPort` contributes compatibility deployments that
    have not yet moved under control-plane management. Forge flock sessions
    remain ordinary sessions and are merged into the session view.
    """

    def __init__(
        self,
        *,
        platform: PlatformRuntimePort,
        discovery: ResidentDiscoveryPort | None = None,
    ) -> None:
        self._platform = platform
        self._discovery = discovery

    async def aclose(self) -> None:
        await self._platform.aclose()

    async def list_ravens(
        self,
        principal: Principal,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return durable and visible compatibility residents as ravens.

        Durable records are authoritative for managed residents. Kubernetes
        discovery remains a compatibility input for externally managed legacy
        residents and is filtered by its explicit visibility metadata.
        """
        managed = await self._platform.list_resident_runtimes(auth_headers, auth_params)
        ravens = [self._managed_to_raven(runtime) for runtime in managed]
        managed_ids = {raven["id"] for raven in ravens}
        for resident in await self._discover_standalone():
            if resident.id in managed_ids or not self._is_discovered_visible(resident, principal):
                continue
            ravens.append(self._standalone_to_raven(resident))
        return ravens

    async def list_profiles(
        self,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return profiles that can actually be deployed on this target."""
        return await self._platform.list_resident_profiles(auth_headers, auth_params)

    async def create_raven(
        self,
        body: dict[str, Any],
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        """Deploy one managed resident and return the Ravn product projection."""
        runtime = await self._platform.create_resident_runtime(
            body,
            auth_headers,
            auth_params,
        )
        return self._managed_to_raven(runtime)

    async def control_raven(
        self,
        ravn_id: str,
        action: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        """Apply a lifecycle action and return the updated Ravn projection."""
        runtime = await self._platform.control_resident_runtime(
            ravn_id,
            action,
            auth_headers,
            auth_params,
        )
        return self._managed_to_raven(runtime)

    async def delete_raven(
        self,
        ravn_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> None:
        """Delete one managed resident through the target control plane."""
        await self._platform.delete_resident_runtime(
            ravn_id,
            auth_headers,
            auth_params,
        )

    async def list_sessions(
        self,
        principal: Principal,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return the caller's live ravn sessions (flock rooms + residents).

        These are the real running sessions you can open and chat with, the
        Ravn-side equivalent of the Volundr live session list — each carries a
        ``chat_endpoint`` (its Skuld room) so the UI can reuse the shared chat.
        """
        sessions = await self._platform.list_forge_sessions(auth_headers, auth_params)
        ravn_sessions = []
        for session in sessions:
            if not isinstance(session, dict):
                continue
            if session.get("workload_type") not in _RAVN_SESSION_WORKLOADS:
                continue
            mapped = self._to_session(session)
            if _is_live_session(mapped):
                ravn_sessions.append(mapped)
        known_ids = {session["id"] for session in ravn_sessions}
        managed = await self._platform.list_resident_runtimes(auth_headers, auth_params)
        for runtime in managed:
            runtime_id = str(runtime.get("id") or "")
            if not runtime_id or runtime_id in known_ids:
                continue
            mapped = self._managed_to_session(runtime)
            if _is_live_session(mapped):
                ravn_sessions.append(mapped)
                known_ids.add(runtime_id)
        for resident in await self._discover_standalone_best_effort():
            if resident.id in known_ids or not self._is_discovered_visible(resident, principal):
                continue
            mapped = self._standalone_to_session(resident)
            if _is_live_session(mapped):
                ravn_sessions.append(mapped)
        return ravn_sessions

    async def get_session(
        self,
        session_id: str,
        principal: Principal,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        """Return one live ravn session by id, or None."""
        if not _RAVN_ID_RE.fullmatch(session_id):
            return None
        session = await self._platform.get_forge_session(
            session_id,
            auth_headers,
            auth_params,
        )
        if session is None:
            return await self._managed_or_standalone_session(
                session_id,
                principal,
                auth_headers,
                auth_params,
            )
        if (
            not isinstance(session, dict)
            or session.get("workload_type") not in _RAVN_SESSION_WORKLOADS
        ):
            return await self._managed_or_standalone_session(
                session_id,
                principal,
                auth_headers,
                auth_params,
            )
        mapped = self._to_session(session)
        if not _is_live_session(mapped):
            return None
        return mapped

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

    async def get_raven(
        self,
        ravn_id: str,
        principal: Principal,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        """Return one caller-visible durable or compatibility resident."""
        if not _RAVN_ID_RE.fullmatch(ravn_id):
            return None
        managed = await self._platform.get_resident_runtime(ravn_id, auth_headers, auth_params)
        if managed is not None:
            return self._managed_to_raven(managed)
        for resident in await self._discover_standalone():
            if resident.id == ravn_id:
                if not self._is_discovered_visible(resident, principal):
                    return None
                return self._standalone_to_raven(resident)
        return None

    async def _discover_standalone(self) -> list[StandaloneResident]:
        """Return discovered standalone residents for authoritative fleet reads."""
        if self._discovery is None:
            return []
        return await self._discovery.list_residents()

    async def _discover_standalone_best_effort(self) -> list[StandaloneResident]:
        """Return compatibility residents without breaking Forge session reads."""
        try:
            return await self._discover_standalone()
        except Exception:
            logger.warning(
                "standalone resident discovery failed; serving forge results only",
                exc_info=True,
            )
            return []

    async def _standalone_session(
        self,
        session_id: str,
        principal: Principal,
    ) -> dict[str, Any] | None:
        for resident in await self._discover_standalone_best_effort():
            if resident.id == session_id:
                if not self._is_discovered_visible(resident, principal):
                    return None
                mapped = self._standalone_to_session(resident)
                if _is_live_session(mapped):
                    return mapped
                return None
        return None

    async def _managed_or_standalone_session(
        self,
        session_id: str,
        principal: Principal,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        managed = await self._platform.get_resident_runtime(
            session_id,
            auth_headers,
            auth_params,
        )
        if managed is not None:
            mapped = self._managed_to_session(managed)
            return mapped if _is_live_session(mapped) else None
        return await self._standalone_session(session_id, principal)

    @staticmethod
    def _managed_to_raven(runtime: dict[str, Any]) -> dict[str, Any]:
        endpoints = runtime.get("endpoints") or []
        chat_endpoint = next(
            (
                endpoint.get("url")
                for endpoint in endpoints
                if isinstance(endpoint, dict) and endpoint.get("kind") == "chat"
            ),
            None,
        )
        observed_state = str(
            runtime.get("observed_state") or runtime.get("observedState") or "pending"
        )
        profile_id = str(runtime.get("profile_id") or runtime.get("profileId") or "")
        return {
            "id": str(runtime.get("id") or ""),
            "persona_name": runtime.get("persona_name") or runtime.get("personaName") or "",
            "resident_name": runtime.get("name") or "",
            "peer_id": "",
            "kind": "resident",
            "status": _MANAGED_RAVEN_STATUS_MAP.get(observed_state, "idle"),
            "model": runtime.get("model") or "",
            "created_at": runtime.get("created_at") or runtime.get("createdAt"),
            "updated_at": runtime.get("updated_at") or runtime.get("updatedAt"),
            "chat_endpoint": chat_endpoint,
            "session_id": str(runtime.get("id") or ""),
            "location": "",
            "deployment": runtime.get("backend") or "",
            "backend": runtime.get("backend") or "",
            "engine": runtime.get("engine") or "",
            "profile_id": profile_id,
            "desired_state": runtime.get("desired_state") or runtime.get("desiredState"),
            "observed_state": observed_state,
            "backend_ref": runtime.get("backend_ref") or runtime.get("backendRef") or {},
            "capabilities": runtime.get("capabilities") or [],
            "conditions": runtime.get("conditions") or [],
            "managed": True,
        }

    @classmethod
    def _managed_to_session(cls, runtime: dict[str, Any]) -> dict[str, Any]:
        raven = cls._managed_to_raven(runtime)
        return {
            "id": raven["id"],
            "ravn_id": raven["id"],
            "persona_name": raven["persona_name"],
            "status": _MANAGED_SESSION_STATUS_MAP.get(raven["observed_state"], "idle"),
            "model": raven["model"],
            "created_at": raven["created_at"],
            "chat_endpoint": raven["chat_endpoint"],
            "title": raven["resident_name"],
            "engine": raven["engine"],
            "capabilities": raven["capabilities"],
        }

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
            "backend": "unknown",
            "engine": "ravn",
            "profile_id": "",
            "desired_state": "running",
            "observed_state": resident.status,
            "backend_ref": {},
            "capabilities": ["chat"],
            "conditions": [],
            "managed": False,
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
            "engine": "ravn",
            "capabilities": ["chat"],
        }

    @staticmethod
    def _is_discovered_visible(resident: StandaloneResident, principal: Principal) -> bool:
        if resident.visibility is InstanceVisibility.SYSTEM:
            return True
        if resident.visibility is InstanceVisibility.TENANT:
            return bool(resident.tenant_id) and resident.tenant_id == principal.tenant_id
        if resident.tenant_id and resident.tenant_id != principal.tenant_id:
            return False
        if resident.owner_id == principal.user_id:
            return True
        return bool(resident.tenant_id) and "volundr:admin" in principal.roles


def _is_live_session(session: dict[str, Any]) -> bool:
    """Return True for sessions worth showing in the live Ravn control room."""
    return str(session.get("status") or "") in _LIVE_SESSION_STATUSES
