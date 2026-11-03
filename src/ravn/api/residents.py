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
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from niuu.adapters.inbound.source_health import SourceFailure, source_failure
from niuu.domain.discovered_residents import discovered_resident_id
from niuu.domain.models import InstanceVisibility, Principal
from niuu.domain.tenancy import DEFAULT_PLATFORM_TENANT_ID, belongs_to_tenant
from ravn.ports.platform_runtime import PlatformRuntimePort
from ravn.ports.resident_discovery import ResidentDiscoveryPort, StandaloneResident
from ravn.ports.resident_logs import ResidentLogPort
from ravn.ports.resident_roster import ResidentRosterPort

logger = logging.getLogger(__name__)


class StandaloneDiscoveryUnavailableError(RuntimeError):
    """Raised when standalone-resident discovery fails and the caller needs
    to know the truth rather than an answer that silently discounts it.

    Session listing tolerates this (Forge sessions are authoritative and
    keep working; the failure is reported additively — see
    ``ResidentDirectory.list_sessions``), but a single-session lookup cannot:
    silently returning "not found" here would misreport an unreachable
    resident as one that never existed. See
    ``.claude/rules/no-fallbacks.md``.
    """


class ResidentNotFoundError(LookupError):
    """No resident with this id is visible to the caller."""


class ResidentSessionsUnavailableError(RuntimeError):
    """The resident exists but has no channels beyond its room.

    A catalog resident gets channels once the Forge discovers it (its record on
    the ``discovered`` backend); the message says so.
    """


class ResidentLogsUnavailableError(RuntimeError):
    """The resident exists but nothing is configured to read its logs.

    The message names what to configure; this is a deployment gap, never an
    empty log.
    """


@dataclass(frozen=True)
class SessionsResult:
    """Live sessions plus any source that failed to contribute.

    Additive, mirroring the ``X-Niuu-Source-Failures`` shape used by Guild's
    aggregate routers (``niuu.adapters.inbound.source_health``) — a failed
    contributor is named, not silently absorbed into a shorter list.
    """

    sessions: list[dict[str, Any]]
    source_failures: list[SourceFailure]


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


def _discovered_key(runtime: dict[str, Any]) -> str:
    """The resident a Forge record backs when the Forge discovered it, else ''."""
    if str(runtime.get("backend") or "") != "discovered":
        return ""
    backend_ref = runtime.get("backend_ref") or runtime.get("backendRef") or {}
    return str(backend_ref.get("key") or "") if isinstance(backend_ref, dict) else ""


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
    have not yet moved under control-plane management. A
    :class:`ResidentRosterPort` contributes the configured catalog residents
    (the Valkyries), which are deployed from infrastructure and so are neither
    managed nor discoverable. Forge flock sessions remain ordinary sessions
    and are merged into the session view.

    Id precedence is managed, then discovered, then rostered: a lower source
    never lists an id a higher one already claims.
    """

    def __init__(
        self,
        *,
        platform: PlatformRuntimePort,
        discovery: ResidentDiscoveryPort | None = None,
        roster: ResidentRosterPort | None = None,
        logs: ResidentLogPort | None = None,
        platform_tenant_id: str = DEFAULT_PLATFORM_TENANT_ID,
    ) -> None:
        self._platform = platform
        self._platform_tenant_id = platform_tenant_id
        self._discovery = discovery
        self._roster_port = roster
        self._logs = logs

    async def aclose(self) -> None:
        await self._platform.aclose()
        if self._logs is not None:
            await self._logs.aclose()

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
        discovered = [*await self._discover_standalone(), *await self._roster_residents()]
        # The Forge's record of a resident it discovered backs that resident's
        # channels; the resident is listed once, as itself.
        backed = {resident.id: resident for resident in discovered}
        backings: dict[str, dict[str, Any]] = {}
        ravens: list[dict[str, Any]] = []
        for runtime in managed:
            key = _discovered_key(runtime)
            if key in backed:
                backings[key] = runtime
                continue
            ravens.append(self._managed_to_raven(runtime))
        managed_ids = {raven["id"] for raven in ravens}
        claimed_ids = set(managed_ids)
        for resident in discovered:
            if resident.id in claimed_ids:
                continue
            claimed_ids.add(resident.id)
            if not self._is_discovered_visible(resident, principal):
                continue
            ravens.append(self._standalone_to_raven(resident, backings.get(resident.id)))
        return ravens

    async def list_system_ravens(self, principal: Principal) -> list[dict[str, Any]]:
        """Return only the residents every member of the caller's org may see.

        For shared views such as the Observatory topology: one picture shown to
        every signed-in user must not be built from any one user's residents, so
        this never asks the platform for user-owned runtimes and keeps only the
        discovered and catalog residents whose visibility is ``system``. A
        resident stays inside the org that owns it, and one that names no org
        belongs to the org running the deployment.
        """
        ravens: list[dict[str, Any]] = []
        claimed_ids: set[str] = set()
        for resident in [*await self._discover_standalone(), *await self._roster_residents()]:
            if resident.id in claimed_ids:
                continue
            claimed_ids.add(resident.id)
            if resident.visibility is not InstanceVisibility.SYSTEM:
                continue
            if not self._is_discovered_visible(resident, principal):
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

    async def get_raven_logs(
        self,
        ravn_id: str,
        *,
        principal: Principal,
        lines: int,
        sources: tuple[str, ...],
        min_level: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        """Return one resident's recent logs.

        A managed resident's come from its control plane. A discovered or
        catalog resident is a plain workload, so its logs are read by its
        placement from the configured log source.
        """
        managed = await self._platform.get_resident_runtime(ravn_id, auth_headers, auth_params)
        if managed is not None:
            return await self._platform.get_resident_logs(
                ravn_id,
                lines=lines,
                sources=sources,
                min_level=min_level,
                auth_headers=auth_headers,
                auth_params=auth_params,
            )
        resident = await self._visible_unmanaged(ravn_id, principal)
        if resident is None:
            raise ResidentNotFoundError(f"no resident {ravn_id!r} is visible to the caller")
        if resident.placement is None:
            raise ResidentLogsUnavailableError(
                f"resident {ravn_id!r} has no placement, so its logs cannot be located: give "
                "its catalog entry a placement {cluster, namespace, workload}, or its discovery "
                "adapter a cluster"
            )
        if self._logs is None:
            raise ResidentLogsUnavailableError(
                f"no resident log source is configured to read {ravn_id!r}: set "
                "resident_logs.adapter (for example "
                "ravn.adapters.resident_logs.loki.LokiResidentLogAdapter with base_url)"
            )
        return await self._logs.read_logs(
            resident.placement,
            lines=lines,
            sources=sources,
            min_level=min_level,
        )

    async def list_resident_sessions(
        self,
        ravn_id: str,
        principal: Principal,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Return native sessions from one managed resident.

        A rostered resident has no engine-owned sessions: its one session is
        its room, listed only when the catalog gives it a chat endpoint. A
        managed resident of the same id still wins.
        """
        rostered = await self._visible_rostered(ravn_id, principal)
        if rostered is not None:
            managed = await self._platform.get_resident_runtime(ravn_id, auth_headers, auth_params)
            if managed is None:
                room = [self._standalone_to_session(rostered)] if rostered.chat_endpoint else []
                backing = await self._discovered_backing(ravn_id, auth_headers, auth_params)
                if backing is None:
                    return room
                runtime_id = str(backing["id"])
                sessions = await self._platform.list_resident_sessions(
                    runtime_id, auth_headers, auth_params
                )
                return room + [
                    self._native_session(session, ravn_id, runtime_id=runtime_id)
                    for session in sessions
                ]
        sessions = await self._platform.list_resident_sessions(ravn_id, auth_headers, auth_params)
        return [self._native_session(session, ravn_id) for session in sessions]

    async def create_resident_session(
        self,
        ravn_id: str,
        body: dict[str, Any],
        principal: Principal,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any]:
        """Create one native session through the resident engine adapter."""
        runtime_id = await self._session_runtime_id(ravn_id, principal, auth_headers, auth_params)
        session = await self._platform.create_resident_session(
            runtime_id, body, auth_headers, auth_params
        )
        return self._native_session(session, ravn_id, runtime_id=runtime_id)

    async def delete_resident_session(
        self,
        ravn_id: str,
        session_id: str,
        principal: Principal,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> None:
        """Delete one native resident session."""
        runtime_id = await self._session_runtime_id(ravn_id, principal, auth_headers, auth_params)
        await self._platform.delete_resident_session(
            runtime_id, session_id, auth_headers, auth_params
        )

    async def _session_runtime_id(
        self,
        ravn_id: str,
        principal: Principal,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> str:
        """The platform runtime whose engine holds this resident's sessions.

        A managed resident's own; for a catalog resident, the record the Forge keeps
        for it once discovered. A catalog resident the Forge has not discovered has
        no channels beyond its room.
        """
        if await self._visible_rostered(ravn_id, principal) is None:
            return ravn_id
        if await self._platform.get_resident_runtime(ravn_id, auth_headers, auth_params):
            return ravn_id
        backing = await self._discovered_backing(ravn_id, auth_headers, auth_params)
        if backing is None:
            raise ResidentSessionsUnavailableError(
                f"resident {ravn_id!r} has no channels beyond its room: the Forge has not "
                "discovered it (configure it on the Forge's discovered resident backend)"
            )
        return str(backing["id"])

    async def _discovered_backing(
        self,
        resident_id: str,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> dict[str, Any] | None:
        """The Forge's record of a resident it discovered, when the caller may see it."""
        return await self._platform.get_resident_runtime(
            str(discovered_resident_id(resident_id)), auth_headers, auth_params
        )

    async def list_sessions(
        self,
        principal: Principal,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
    ) -> SessionsResult:
        """Return the caller's live ravn sessions (flock rooms + residents).

        These are the real running sessions you can open and chat with, the
        Ravn-side equivalent of the Volundr live session list — each carries a
        ``chat_endpoint`` (its Skuld room) so the UI can reuse the shared chat.

        Forge sessions are authoritative and this keeps returning them even
        when a resident-session or standalone-discovery source fails — but
        that failure is reported in ``source_failures`` instead of being
        silently absorbed into a shorter list.
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
        source_failures: list[SourceFailure] = []
        managed = await self._platform.list_resident_runtimes(auth_headers, auth_params)
        for runtime in managed:
            runtime_id = str(runtime.get("id") or "")
            if not runtime_id:
                continue
            runtime_name = str(
                runtime.get("persona_name")
                or runtime.get("personaName")
                or runtime.get("name")
                or runtime_id
            )
            capabilities = runtime.get("capabilities") or []
            if "session.list" in capabilities:
                try:
                    native_sessions = await self._platform.list_resident_sessions(
                        runtime_id, auth_headers, auth_params
                    )
                except Exception as exc:
                    logger.warning(
                        "native resident session discovery failed for %s",
                        runtime_id,
                        exc_info=True,
                    )
                    source_failures.append(
                        source_failure(
                            instance_id=runtime_id,
                            name=runtime_name,
                            error=str(exc) or exc.__class__.__name__,
                        )
                    )
                    continue
                for native in native_sessions:
                    mapped_native = self._native_session(native, runtime_id, runtime_name)
                    if mapped_native["id"] and _is_live_session(mapped_native):
                        ravn_sessions.append(mapped_native)
                        known_ids.add(mapped_native["id"])
                continue
            if runtime_id in known_ids:
                continue
            mapped = self._managed_to_session(runtime)
            if _is_live_session(mapped):
                ravn_sessions.append(mapped)
                known_ids.add(runtime_id)
        residents, discovery_failure = await self._discover_standalone_best_effort()
        if discovery_failure is not None:
            source_failures.append(discovery_failure)
        claimed_ids = set(known_ids)
        claimed_ids.update(str(runtime.get("id") or "") for runtime in managed)
        for resident in residents:
            claimed_ids.add(resident.id)
            if resident.id in known_ids or not self._is_discovered_visible(resident, principal):
                continue
            mapped = self._standalone_to_session(resident)
            if _is_live_session(mapped):
                ravn_sessions.append(mapped)
        rostered, roster_failure = await self._roster_residents_best_effort()
        if roster_failure is not None:
            source_failures.append(roster_failure)
        for resident in rostered:
            if resident.id in claimed_ids or not self._is_discovered_visible(resident, principal):
                continue
            mapped = self._standalone_to_session(resident)
            if resident.chat_endpoint and _is_live_session(mapped):
                ravn_sessions.append(mapped)
        return SessionsResult(sessions=ravn_sessions, source_failures=source_failures)

    async def get_session(
        self,
        session_id: str,
        principal: Principal,
        auth_headers: dict[str, str],
        auth_params: dict[str, str],
        *,
        ravn_id: str | None = None,
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
            managed_or_standalone = await self._managed_or_standalone_session(
                session_id,
                principal,
                auth_headers,
                auth_params,
            )
            if managed_or_standalone is not None:
                return managed_or_standalone
            managed = await self._platform.list_resident_runtimes(auth_headers, auth_params)
            for runtime in managed:
                capabilities = runtime.get("capabilities") or []
                runtime_id = str(runtime.get("id") or "")
                if not runtime_id or "session.list" not in capabilities:
                    continue
                if ravn_id is not None and runtime_id != ravn_id:
                    continue
                try:
                    native_sessions = await self._platform.list_resident_sessions(
                        runtime_id, auth_headers, auth_params
                    )
                except Exception:
                    continue
                for native in native_sessions:
                    if str(native.get("id") or "") == session_id:
                        return self._native_session(
                            native,
                            runtime_id,
                            str(
                                runtime.get("persona_name")
                                or runtime.get("personaName")
                                or runtime.get("name")
                                or "resident-agent"
                            ),
                        )
            return None
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
        result = await self._platform.stop_forge_session(
            session_id,
            auth_headers,
            auth_params,
        )
        if result is None:
            return None
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
        resident = await self._visible_unmanaged(ravn_id, principal)
        if resident is None:
            return None
        backing = await self._discovered_backing(ravn_id, auth_headers, auth_params)
        return self._standalone_to_raven(resident, backing)

    async def _discover_standalone(self) -> list[StandaloneResident]:
        """Return discovered standalone residents for authoritative fleet reads."""
        if self._discovery is None:
            return []
        return await self._discovery.list_residents()

    async def _roster_residents(self) -> list[StandaloneResident]:
        """Return the configured roster; a roster that cannot answer raises."""
        if self._roster_port is None:
            return []
        return await self._roster_port.list_residents()

    async def _visible_unmanaged(
        self,
        ravn_id: str,
        principal: Principal,
    ) -> StandaloneResident | None:
        """Return the discovered, else rostered, resident with this id when visible."""
        for resident in [*await self._discover_standalone(), *await self._roster_residents()]:
            if resident.id == ravn_id:
                return resident if self._is_discovered_visible(resident, principal) else None
        return None

    async def _visible_rostered(
        self,
        ravn_id: str,
        principal: Principal,
    ) -> StandaloneResident | None:
        """Return the rostered resident with this id when the caller may see it."""
        for resident in await self._roster_residents():
            if resident.id == ravn_id and self._is_discovered_visible(resident, principal):
                return resident
        return None

    async def _roster_residents_best_effort(
        self,
    ) -> tuple[list[StandaloneResident], SourceFailure | None]:
        """Roster counterpart of :meth:`_discover_standalone_best_effort`.

        Forge sessions stay authoritative for the session list, so a roster
        failure does not break it, but it is returned to be reported rather
        than absorbed into a shorter list.
        """
        try:
            return await self._roster_residents(), None
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            logger.error(
                "Resident roster failed (%s); serving the other sources only. Rostered "
                "residents will be missing from this session list until the roster answers.",
                message,
                exc_info=True,
            )
            return [], source_failure(
                instance_id="resident-roster",
                name="Resident roster",
                error=message,
            )

    async def _discover_standalone_best_effort(
        self,
    ) -> tuple[list[StandaloneResident], SourceFailure | None]:
        """Return compatibility residents without breaking Forge session reads.

        Forge sessions are authoritative and must keep working even when the
        standalone-discovery adapter chain is down, so this deliberately does
        not raise. The old behavior swallowed the failure entirely — logged
        at `warning` with no further trace, and the caller got back the same
        `[]` it would get from "no standalone discovery configured". That
        made ``/api/v1/ravn/status`` report healthy with an undercount: the
        session list was simply shorter, with nothing to say why. The
        failure is now returned alongside the (possibly empty) result so
        callers can report it additively (see
        ``.claude/rules/no-fallbacks.md`` — this is the "protocol-level
        optional read... and is reported" escape, and only qualifies because
        it *is* reported here).
        """
        try:
            return await self._discover_standalone(), None
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            logger.error(
                "Standalone resident discovery failed (%s); serving forge session results "
                "only. Standalone residents will be missing from this session list until "
                "the discovery adapter is reachable again.",
                message,
                exc_info=True,
            )
            return [], source_failure(
                instance_id="standalone-discovery",
                name="Standalone resident discovery",
                error=message,
            )

    async def _standalone_session(
        self,
        session_id: str,
        principal: Principal,
    ) -> dict[str, Any] | None:
        """Look up one standalone resident by id.

        Unlike the best-effort list used for session merging, a single
        lookup that cannot determine the truth must not silently answer
        "not found" — that would misreport an unreachable resident as one
        that never existed. Discovery failures raise here instead (see
        ``StandaloneDiscoveryUnavailableError``); the router maps it to a
        503.
        """
        try:
            residents = await self._discover_standalone()
        except Exception as exc:
            raise StandaloneDiscoveryUnavailableError(
                f"Standalone resident discovery is unavailable: {exc}"
            ) from exc
        for resident in residents:
            if resident.id == session_id:
                if not self._is_discovered_visible(resident, principal):
                    return None
                mapped = self._standalone_to_session(resident)
                if _is_live_session(mapped):
                    return mapped
                return None
        for resident in await self._roster_residents():
            if resident.id == session_id:
                if not self._is_discovered_visible(resident, principal):
                    return None
                mapped = self._standalone_to_session(resident)
                if resident.chat_endpoint and _is_live_session(mapped):
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
        flock_id = str(runtime.get("flock_id") or runtime.get("flockId") or "")
        flock_member_id = str(runtime.get("flock_member_id") or runtime.get("flockMemberId") or "")
        flock_peer_id = str(runtime.get("flock_peer_id") or runtime.get("flockPeerId") or "")
        realm_id = str(runtime.get("realm_id") or runtime.get("realmId") or "")
        endpoint_urls = {
            str(endpoint.get("kind") or ""): str(endpoint.get("url") or "")
            for endpoint in endpoints
            if isinstance(endpoint, dict)
        }
        return {
            "id": str(runtime.get("id") or ""),
            "owner_id": runtime.get("owner_id") or runtime.get("ownerId") or "",
            "tenant_id": runtime.get("tenant_id") or runtime.get("tenantId") or "",
            "persona_name": runtime.get("persona_name") or runtime.get("personaName") or "",
            "resident_name": runtime.get("name") or "",
            "peer_id": flock_peer_id,
            "kind": "resident",
            "status": _MANAGED_RAVEN_STATUS_MAP.get(observed_state, "idle"),
            "model": runtime.get("model") or "",
            "created_at": runtime.get("created_at") or runtime.get("createdAt"),
            "updated_at": runtime.get("updated_at") or runtime.get("updatedAt"),
            "chat_endpoint": chat_endpoint,
            # Published by Volundr for residents reachable over A2A
            # (volundr.adapters.inbound.rest_resident_a2a).
            "a2a_card_url": endpoint_urls.get("a2a-card") or None,
            "a2a_endpoint_url": endpoint_urls.get("a2a") or None,
            "session_id": str(runtime.get("id") or ""),
            "location": "",
            # A managed resident runs on the instance that manages it, which
            # the aggregate names; only residents placed elsewhere say more.
            "cluster": "",
            "deployment": runtime.get("backend") or "",
            "backend": runtime.get("backend") or "",
            "engine": runtime.get("engine") or "",
            "profile_id": profile_id,
            "flock_id": flock_id,
            "flock_member_id": flock_member_id,
            "flock_role": runtime.get("flock_role") or runtime.get("flockRole") or "",
            "flock_peer_id": flock_peer_id,
            "realm_id": realm_id,
            "desired_state": runtime.get("desired_state") or runtime.get("desiredState"),
            "observed_state": observed_state,
            "backend_ref": runtime.get("backend_ref") or runtime.get("backendRef") or {},
            "endpoints": endpoints,
            "capabilities": runtime.get("capabilities") or [],
            "conditions": runtime.get("conditions") or [],
            "message_count": runtime.get("message_count") or runtime.get("messageCount") or 0,
            "tokens_used": runtime.get("tokens_used") or runtime.get("tokensUsed") or 0,
            "cost": runtime.get("cost") or 0,
            "managed": True,
            "instance_id": runtime.get("instance_id") or runtime.get("instanceId") or "",
            "instance_name": runtime.get("instance_name") or runtime.get("instanceName") or "",
            "instance_slug": runtime.get("instance_slug") or runtime.get("instanceSlug") or "",
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
            "message_count": raven["message_count"],
            "tokens_used": raven["tokens_used"],
            "cost": raven["cost"],
            "instance_id": raven["instance_id"],
            "instance_name": raven["instance_name"],
            "instance_slug": raven["instance_slug"],
            "flock_id": raven["flock_id"],
            "flock_member_id": raven["flock_member_id"],
            "flock_role": raven["flock_role"],
            "flock_peer_id": raven["flock_peer_id"],
        }

    @staticmethod
    def _native_session(
        session: dict[str, Any],
        ravn_id: str,
        persona_name: str = "resident-agent",
        *,
        runtime_id: str = "",
    ) -> dict[str, Any]:
        """``runtime_id`` names the platform runtime serving the chat, when not ``ravn_id``."""
        session_id = str(session.get("id") or "")
        engine_status = str(session.get("status") or "idle")
        return {
            "id": session_id,
            "ravn_id": ravn_id,
            "persona_name": persona_name,
            "status": "failed" if engine_status == "failed" else "running",
            "model": session.get("model") or "",
            "created_at": session.get("created_at") or session.get("createdAt"),
            "updated_at": session.get("updated_at") or session.get("updatedAt"),
            "chat_endpoint": f"/s/{runtime_id or ravn_id}/sessions/{session_id}/session",
            "title": session.get("title") or "",
            "message_count": session.get("message_count") or session.get("messageCount") or 0,
            "tokens_used": session.get("tokens_used") or session.get("tokensUsed") or 0,
            "cost": session.get("cost") or 0,
            "instance_id": session.get("instance_id") or session.get("instanceId") or "",
            "instance_name": session.get("instance_name") or session.get("instanceName") or "",
            "instance_slug": session.get("instance_slug") or session.get("instanceSlug") or "",
        }

    @staticmethod
    def _standalone_to_raven(
        resident: StandaloneResident,
        backing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``backing`` is the Forge's record of the resident, which brings its channels."""
        session_capabilities = [
            capability
            for capability in (backing or {}).get("capabilities") or []
            if str(capability).startswith("session.") and capability not in resident.capabilities
        ]
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
            "owner_id": resident.owner_id,
            "tenant_id": resident.tenant_id,
            "location": resident.location,
            "cluster": resident.placement.cluster if resident.placement else "",
            "deployment": resident.deployment,
            "backend": "unknown",
            "engine": "ravn",
            "profile_id": "",
            "desired_state": "running",
            "observed_state": resident.status,
            "backend_ref": {},
            "capabilities": [*resident.capabilities, *session_capabilities],
            "conditions": list(resident.conditions),
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
            "capabilities": list(resident.capabilities),
        }

    def _is_discovered_visible(self, resident: StandaloneResident, principal: Principal) -> bool:
        if not belongs_to_tenant(
            resident.tenant_id,
            principal.tenant_id,
            platform_tenant_id=self._platform_tenant_id,
        ):
            return False
        if resident.visibility is InstanceVisibility.SYSTEM:
            return True
        if resident.visibility is InstanceVisibility.TENANT:
            return bool(resident.tenant_id)
        if resident.owner_id == principal.user_id:
            return True
        return bool(resident.tenant_id) and "volundr:admin" in principal.roles


def _is_live_session(session: dict[str, Any]) -> bool:
    """Return True for sessions worth showing in the live Ravn control room."""
    return str(session.get("status") or "") in _LIVE_SESSION_STATUSES
