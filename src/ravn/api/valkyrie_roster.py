"""Resident roster over the configured Valkyrie catalog.

The Valkyries are deployed straight from infrastructure, so neither Forge nor
cluster discovery lists them. The platform does hold an authoritative catalog
of them — the dashboard environment catalog — and the dashboard projection
keeps each one's live state from NATS telemetry. This adapter implements
:class:`~ravn.ports.resident_roster.ResidentRosterPort` over exactly those two
sources: identity, chat room and visibility come from the catalog, and status
comes from what telemetry actually observed, never from a configured default.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from niuu.domain.models import InstanceVisibility
from ravn.api.valkyrie_config import (
    ValkyrieDashboardConfig,
    configured_environment_records,
    roster_fields,
)
from ravn.api.valkyrie_projection_common import _environment_id, _field, _valkyrie_id
from ravn.ports.resident_discovery import ResidentPlacement, StandaloneResident

#: Fleet vocabulary shared with the other standalone residents.
STATUS_ACTIVE = "active"
STATUS_IDLE = "idle"

#: Reported as the resident's deployment so the fleet UI can tell the source.
VALKYRIE_DEPLOYMENT = "valkyrie"

_CONDITION_OBSERVED = "Observed"


class DashboardSource(Protocol):
    """The slice of the dashboard projection the roster reads live state from."""

    def dashboard(self) -> dict[str, Any]:
        """Return the dashboard with telemetry already merged into each Valkyrie."""
        raise NotImplementedError


@dataclass(frozen=True)
class _CatalogEntry:
    valkyrie_id: str
    name: str
    environment_name: str
    persona: str
    chat_endpoint: str | None
    visibility: InstanceVisibility
    owner_id: str
    tenant_id: str
    placement: ResidentPlacement | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ValkyrieCatalogRoster:
    """Lists every catalog Valkyrie as a standalone-resident shaped entry."""

    def __init__(
        self,
        *,
        dashboard: DashboardSource,
        config: ValkyrieDashboardConfig,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._dashboard = dashboard
        self._stale_after_seconds = config.roster_stale_after_seconds
        self._clock = clock
        self._entries = _catalog_entries(config)

    async def list_residents(self) -> list[StandaloneResident]:
        """Return the catalog Valkyries with their observed state."""
        if not self._entries:
            return []
        live = {
            str(entry.get("id") or ""): entry
            for entry in self._dashboard.dashboard().get("valkyries", [])
            if isinstance(entry, dict)
        }
        now = self._clock()
        return [self._resident(entry, live.get(entry.valkyrie_id), now) for entry in self._entries]

    def _resident(
        self,
        entry: _CatalogEntry,
        live: dict[str, Any] | None,
        now: datetime,
    ) -> StandaloneResident:
        status, condition, last_observed_at = self._observed_state(live, now)
        return StandaloneResident(
            id=entry.valkyrie_id,
            resident_name=entry.name,
            persona_name=entry.persona,
            status=status,
            chat_endpoint=entry.chat_endpoint,
            location=entry.environment_name,
            updated_at=last_observed_at,
            visibility=entry.visibility,
            owner_id=entry.owner_id,
            tenant_id=entry.tenant_id,
            deployment=VALKYRIE_DEPLOYMENT,
            capabilities=["chat"] if entry.chat_endpoint else [],
            conditions=[condition],
            placement=entry.placement,
        )

    def _observed_state(
        self,
        live: dict[str, Any] | None,
        now: datetime,
    ) -> tuple[str, dict[str, Any], str | None]:
        """Map the projection's observation onto the fleet status vocabulary.

        Observed within the configured bound is ``active``. Anything else is
        ``idle`` — telemetry is the only evidence of life, so a Valkyrie that
        was never observed (or went quiet) is not claimed to be running — and
        the ``Observed`` condition says which of those it is.
        """
        observed = str(live.get("lastObservedAt") or "") if live else ""
        if not live or live.get("identitySource") != "observed" or not observed:
            return (
                STATUS_IDLE,
                _condition(
                    "unknown",
                    "NeverObserved",
                    "No telemetry has been observed from this Valkyrie since the API started.",
                ),
                None,
            )
        try:
            observed_dt = datetime.fromisoformat(observed)
        except ValueError:
            return (
                STATUS_IDLE,
                _condition(
                    "unknown",
                    "UnparseableTimestamp",
                    f"Last telemetry timestamp {observed!r} is not an ISO-8601 time.",
                ),
                observed,
            )
        if observed_dt.tzinfo is None:
            observed_dt = observed_dt.replace(tzinfo=UTC)
        age_seconds = (now - observed_dt).total_seconds()
        if age_seconds <= self._stale_after_seconds:
            return (
                STATUS_ACTIVE,
                _condition(
                    "true",
                    "Fresh",
                    f"Telemetry observed {age_seconds:.0f}s ago.",
                    transitioned_at=observed,
                ),
                observed,
            )
        return (
            STATUS_IDLE,
            _condition(
                "false",
                "Stale",
                f"Last telemetry was {age_seconds:.0f}s ago; the bound is "
                f"{self._stale_after_seconds:.0f}s.",
                transitioned_at=observed,
            ),
            observed,
        )


def _condition(
    status: str,
    reason: str,
    message: str,
    *,
    transitioned_at: str | None = None,
) -> dict[str, Any]:
    condition: dict[str, Any] = {
        "type": _CONDITION_OBSERVED,
        "status": status,
        "reason": reason,
        "message": message,
    }
    if transitioned_at:
        condition["lastTransitionAt"] = transitioned_at
    return condition


def _catalog_entries(config: ValkyrieDashboardConfig) -> list[_CatalogEntry]:
    entries: list[_CatalogEntry] = []
    seen: set[str] = set()
    for record in configured_environment_records(config):
        environment_id = _environment_id(record)
        valkyrie = _field(record, "valkyrie", default={})
        combined = {**record, **(valkyrie if isinstance(valkyrie, dict) else {})}
        valkyrie_id = _valkyrie_id(combined, environment_id)
        if valkyrie_id in seen:
            raise ValueError(
                f"Valkyrie dashboard catalog lists valkyrie id {valkyrie_id!r} more than once; "
                "each Valkyrie needs a unique valkyrieId"
            )
        seen.add(valkyrie_id)
        fields = roster_fields(record, source="catalog")
        entries.append(
            _CatalogEntry(
                valkyrie_id=valkyrie_id,
                name=str(_field(combined, "valkyrieName", "valkyrie_name", default=valkyrie_id)),
                environment_name=str(_field(record, "name", default=environment_id)),
                persona=str(_field(combined, "persona", default="valkyrie")),
                chat_endpoint=fields.chat_endpoint,
                visibility=fields.visibility,
                owner_id=fields.owner_id,
                tenant_id=fields.tenant_id,
                placement=fields.placement,
            )
        )
    return entries
