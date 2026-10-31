"""The Guild a host belongs to, and how that Guild's instance registry names it.

A Forge node's own notification feed covers only that node. The Guild facade
(``niuu.adapters.inbound.rest_volundr``) merges every member's feed and keys its
read state by the registry's instance id, so a client that talks to a node
directly needs both values to find the merged feed and its own place in it:
``GET /api/v1/forge/guild`` answers them, and the node stamps its instance on
every notification it serves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GuildInstance:
    """This host's entry in the Guild's instance registry (``niuu_instances``)."""

    id: str
    name: str
    slug: str

    def fields(self) -> dict[str, str]:
        """The ``instance_*`` fields the Guild facade puts on every aggregated item."""
        return {
            "instance_id": self.id,
            "instance_name": self.name,
            "instance_slug": self.slug,
        }


@dataclass(frozen=True)
class GuildMembership:
    """The Guild's public origin and, when known, this host's instance in it."""

    guild_url: str
    instance: GuildInstance | None = None

    def wire(self) -> dict[str, Any]:
        """The ``GET /api/v1/forge/guild`` body."""
        return {
            "guild_url": self.guild_url,
            "instance_id": self.instance.id if self.instance else None,
            "instance_name": self.instance.name if self.instance else None,
            "instance_slug": self.instance.slug if self.instance else None,
        }
