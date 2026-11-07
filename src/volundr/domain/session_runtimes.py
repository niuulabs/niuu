"""How a host offers to run a session: the runtimes a launch may choose between."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from volundr.domain.compute import HostSelector


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

#: The workload_config key naming the tags a session's machine must carry. The Guild puts
#: the part of a ``target_tags`` selector the chosen Forge's own tags do not cover here.
HOST_SELECTOR_KEY = "host_selector"


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
    """One runtime a launch on this host may choose."""

    runtime: SessionRuntimeKind
    # The exact execution (id@revision) the choice selects; sent back as
    # workload_config.execution_profile.
    execution_profile: str
    default: bool
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


def requested_host_selector(workload_config: dict) -> HostSelector:
    """The tags a launch's machine must carry; raises ValueError on a malformed request."""
    raw = workload_config.get(HOST_SELECTOR_KEY)
    if raw is None:
        return HostSelector()
    if not isinstance(raw, dict):
        raise ValueError(f'{HOST_SELECTOR_KEY} must be {{"tags": [...], "match": "all"|"any"}}')
    try:
        return HostSelector.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"{HOST_SELECTOR_KEY} is invalid: {exc.errors()[0]['msg']}") from exc
