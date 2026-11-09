"""A run's unit on a VM: the members one VM, or one slot of a VM, hosts with the root.

A workflow run is one unit of compute (``docs/design/workflow-mesh-handoff.md``). On a VM
the unit is the VM (container runtime, one session per VM) or one slot of it (container or
host runtime): the unit root's session is bound to the machine or slot as any session is,
and every member of the run is another Forge session whose Skuld runs beside the root's, in
the same machine or slot, started with it.

This module is what the pod manager and the VM runtimes share about that:

* ``HostedMemberLaunch``: a member and the spec its own contributor pipeline produced;
* the ``spec.values`` entry (``HOSTED_MEMBERS_KEY``) that carries the members from the pod
  manager to the runtime that builds the launch payload, as plain JSON, never a credential;
* the names a member's container or process, directory and mount have in the guest;
* ``HostedUnitVmRuntime``: what a runtime must offer to host a unit.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.compute import MachineBootstrap, SlotLease
from volundr.domain.models import PodSpecAdditions, SessionSpec
from volundr.domain.ports import (  # noqa: F401  (the seam lives in the port)
    HostedMemberLaunch,
    HostedUnitError,
)

#: ``spec.values`` key the pod manager puts a unit's members under for the runtime.
HOSTED_MEMBERS_KEY = "hostedMembers"

#: Guest directory holding one directory per member, each with that member's Forge session
#: credential. It is never under the root's own credential directory, which the root's
#: container mounts whole: a member's directory is mounted into that member alone.
FORGE_SESSIONS_DIRECTORY = "/etc/niuu/forge-sessions"

#: The mesh directory of the unit (a tmpfs of the guest, new on every start) and where every
#: Skuld of the unit sees it. Their registry discovery file lives in it.
UNIT_MESH_SOURCE = "/run/niuu/session/mesh"
UNIT_MESH_TARGET = "/run/niuu/mesh"
UNIT_MESH_REGISTRY = f"{UNIT_MESH_TARGET}/peers.json"


def member_container(base: str, port: int) -> str:
    """The name of a member's container (or unit prefix) next to the root's ``base``."""
    return f"{base}-m{port}"


def member_credential_directory(member_id: str | UUID) -> str:
    """Guest directory holding the one credential file of a member."""
    return f"{FORGE_SESSIONS_DIRECTORY}/{member_id}"


def member_entry(member: HostedMemberLaunch) -> dict[str, Any]:
    """A member as the pod manager hands it to the runtime: JSON, no credential."""
    session = member.session
    if session.hosted_port is None:
        raise HostedUnitError(
            f"Session {session.id} is hosted but has no broker port; the unit start assigns one"
        )
    return {
        "id": str(session.id),
        "name": session.name,
        "model": session.model,
        "owner_id": session.owner_id or "",
        "tenant_id": session.tenant_id or "",
        "port": session.hosted_port,
        "values": copy.deepcopy(member.spec.values),
        "pod": {
            "env": [dict(entry) for entry in member.spec.pod_spec.env],
            "volumes": [copy.deepcopy(v) for v in member.spec.pod_spec.volumes],
            "volume_mounts": [copy.deepcopy(v) for v in member.spec.pod_spec.volume_mounts],
            "init_containers": [copy.deepcopy(v) for v in member.spec.pod_spec.init_containers],
            "extra_containers": [copy.deepcopy(v) for v in member.spec.pod_spec.extra_containers],
            "service_account": member.spec.pod_spec.service_account or "",
        },
        "forge_session": member.spec.forge_session is not None,
    }


def member_spec(entry: Mapping[str, Any]) -> SessionSpec:
    """The member's ``SessionSpec`` as the runtime builds its launch from it."""
    pod = entry["pod"]
    return SessionSpec(
        values=copy.deepcopy(dict(entry["values"])),
        pod_spec=PodSpecAdditions(env=tuple(dict(e) for e in pod["env"])),
    )


def require_same_pod(root: SessionSpec, entry: Mapping[str, Any]) -> None:
    """Refuse a member whose pod differs from the root's: a unit has one pod.

    The unit's volumes, mounts, init containers, sidecars and ServiceAccount come from the
    root's spec and are shared by every Skuld of the unit. A member that declares others
    would silently run without them, so the unit does not start.
    """
    pod = entry["pod"]
    wanted = {
        "volumes": [dict(v) for v in root.pod_spec.volumes],
        "volume_mounts": [dict(v) for v in root.pod_spec.volume_mounts],
        "init_containers": [dict(v) for v in root.pod_spec.init_containers],
        "extra_containers": [dict(v) for v in root.pod_spec.extra_containers],
        "service_account": root.pod_spec.service_account or "",
    }
    for key, expected in wanted.items():
        if pod[key] != expected:
            raise HostedUnitError(
                f"Member session {entry['id']} declares its own {key.replace('_', ' ')}, which "
                "differ from the unit root's: the members of a run share one VM and so one pod "
                "layout. Give the members and the root the same integrations and secrets"
            )


@runtime_checkable
class HostedUnitVmRuntime(Protocol):
    """A VM runtime that can host a unit: the root and members in one machine or slot.

    Every operation is about one member of the unit bound to ``lease`` (the root's lease);
    the runtime reads the member's place from the unit's stored launch payload.
    """

    async def member_running(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> bool:
        """Whether the member's Skuld process or container is running."""

    async def member_target(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> SessionProxyTarget:
        """Where Forge reaches the member's broker."""

    async def member_ready(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> bool:
        """Whether the member's broker answers its health check."""

    async def restart_member(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> None:
        """Start the member's Skuld again, in place, under its new launch credential."""

    async def stop_member(
        self, lease: SlotLease, bootstrap: MachineBootstrap, member_id: UUID
    ) -> None:
        """Stop the member's Skuld and harness; the unit and the other members keep running."""
