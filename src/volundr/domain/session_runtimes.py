"""What a host runs sessions as: the one runtime of a VM Forge, with its readiness."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SessionRuntimeKind(StrEnum):
    """What a session runs in on its machine."""

    # Its own container image and root inside it.
    CONTAINER = "container"
    # An OpenShell sandbox whose policy is the boundary.
    OPENSHELL = "openshell"
    # Directly on the machine, with the machine's own tools.
    HOST = "host"


class SessionGrant(StrEnum):
    """Extra access a runtime may give one session. Either is root-equivalent,
    so a runtime that offers them gives such a session a machine to itself."""

    SUDO = "sudo"
    DOCKER = "docker"


#: The workload_config key a launch names its grants in.
RUNTIME_GRANTS_KEY = "runtime_grants"


class ReadinessState(StrEnum):
    # A prepared machine is free: the session starts in seconds.
    WARM = "warm"
    # No prepared machine is free, but the pool may boot one (minutes).
    BOOT = "boot"
    # The pool can take no new session until capacity frees up.
    FULL = "full"


@dataclass(frozen=True)
class RuntimeReadiness:
    state: ReadinessState
    # Free prepared machines that can take this runtime's session now.
    warm: int
    # Where capacity is raised when the pool is full; None otherwise.
    remedy: str | None = None


@dataclass(frozen=True)
class SessionRuntimeOffer:
    """The runtime this host runs every session as. A host has exactly one."""

    runtime: SessionRuntimeKind
    grants: tuple[SessionGrant, ...]
    readiness: RuntimeReadiness


def requested_grants(workload_config: dict) -> frozenset[SessionGrant]:
    """The grants a launch asked for; raises ValueError on a malformed request."""
    raw = workload_config.get(RUNTIME_GRANTS_KEY)
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"{RUNTIME_GRANTS_KEY} must be a list of grant names")
    try:
        return frozenset(SessionGrant(item) for item in raw)
    except ValueError as exc:
        known = ", ".join(grant.value for grant in SessionGrant)
        raise ValueError(f"Unknown {RUNTIME_GRANTS_KEY} entry; known grants: {known}") from exc
