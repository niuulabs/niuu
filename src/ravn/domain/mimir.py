"""Ravn-specific Mímir domain models.

These types configure how Ravn fans out across multiple Mímir instances.
They are separate from the shared ``niuu.domain.mimir`` models which define
the wire contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from niuu.ports.mimir import MimirPort


@dataclass
class MimirAuth:
    """Authentication configuration for a Mímir instance.

    Attributes:
        type:         Auth mechanism: ``bearer``, ``workload``, or ``spiffe``.
        token:        Bearer token value (when type=``bearer``).
        trust_domain: SPIFFE trust domain (when type=``spiffe``).
    """

    type: Literal["bearer", "workload", "spiffe"] = "bearer"
    token: str | None = None
    token_file: str | None = None
    exchange_url: str | None = None
    audiences: tuple[str, ...] = ()
    trust_domain: str | None = None


@dataclass
class MimirMount:
    """A single Mímir instance mounted in the composite adapter.

    Attributes:
        name:          Logical name, e.g. ``local``, ``shared``, ``kanuck``.
        port:          The MimirPort implementation for this instance.
        role:          Instance role: ``shared``, ``local``, or ``domain``.
        categories:    Category filter — write routing uses this to decide
                       which mount receives writes for a given path prefix.
                       ``None`` means the mount accepts all categories.
        read_priority: Read order — lower value is queried first.
                       Conventional values: local=0, shared=1, domain=2.
    """

    name: str
    port: MimirPort
    role: Literal["shared", "local", "domain"]
    categories: list[str] | None = None  # None = all categories
    read_priority: int = 0


@dataclass
class WriteRouting:
    """Config-driven write routing for the CompositeMimirAdapter.

    Attributes:
        rules:   Ordered list of ``(path_prefix, list[mount_name])`` pairs.
                 The first matching prefix wins.
        default: Mount name(s) to use when no prefix matches.
    """

    rules: list[tuple[str, list[str]]] = field(default_factory=list)
    default: list[str] = field(default_factory=lambda: ["local"])

    def resolve(self, path: str, explicit: str | None = None) -> list[str]:
        """Return the list of mount names that should receive a write for *path*.

        Args:
            path:     Wiki page path, e.g. ``"technical/ravn/tools.md"``.
            explicit: Agent-supplied override (bypasses all routing rules).
        """
        if explicit is not None:
            return [explicit]

        for prefix, mounts in self.rules:
            if path.startswith(prefix):
                return mounts

        return self.default
