"""Port for the configured roster of catalog residents.

A roster is an authoritative, operator-configured list of residents (the
Valkyrie catalog being the one in production) that are deployed straight from
infrastructure rather than through Forge or a discoverable cluster label. The
resident directory merges it into the fleet read paths next to managed and
discovered residents.

Unlike :class:`~ravn.ports.resident_discovery.ResidentDiscoveryPort`, a roster
is not found by probing anything: it is configuration, and each entry carries
the live state its implementation actually observed.
"""

from __future__ import annotations

from typing import Protocol

from ravn.ports.resident_discovery import StandaloneResident


class ResidentRosterPort(Protocol):
    """Read-only surface over the configured resident roster."""

    async def list_residents(self) -> list[StandaloneResident]:
        """Return every rostered resident with its currently observed state."""
        raise NotImplementedError
