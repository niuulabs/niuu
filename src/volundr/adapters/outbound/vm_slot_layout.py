"""Where one session slot of a VM lives, and how a VM runtime addresses it.

A runtime configured with ``slot_isolation`` runs every session in a slot of
its VM (``vm_slot_supervisor``): its own user, directories, network and PID
namespaces and cgroup. This module derives everything about slot ``n`` from the
configuration and the allocation: ids, addresses, names and the guest paths the
session's files move to. Without ``slot_isolation`` there is no layout and the
runtime keeps one session per VM at the paths below, as before slots.
"""

from __future__ import annotations

import copy
import ipaddress
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from volundr.adapters.outbound.vm_slot_supervisor import ALLOCATION_LABEL, SLOT_LABEL

#: Guest paths of a VM's single session, before slots (and without slot isolation).
SESSION_DATA = "/var/lib/niuu/session"
SESSION_LAUNCH = "/etc/niuu/session-launch.json"
SESSION_SECRETS = "/etc/niuu/session-secrets"
FORGE_SESSION_DIRECTORY = "/etc/niuu/forge-session"
#: The metadata guest agent's configuration (``guest_credential_delivery``).
GUEST_AGENT_DIRECTORY = "/etc/niuu/guest-agent"
#: Pod volumes Kubernetes would keep in memory: guest tmpfs.
SESSION_RUNTIME = "/run/niuu/session"
SLOT_DATA_BASE = "/var/lib/niuu/slots"
SLOT_RUNTIME_BASE = "/run/niuu/slots"
#: Addresses per slot: network, gateway (the VM's end), the slot, broadcast.
_ADDRESSES_PER_SLOT = 4
_MAX_ID = 2**32 - 2


class SlotIsolation(BaseModel):
    """``compute.runtime.kwargs.slot_isolation``: separate the sessions of a VM.

    The limits have no default: each is a number or ``null`` for "no limit",
    written down by the operator for the machine profile.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    uid_base: int = Field(
        default=61000,
        ge=1000,
        description="Slot n runs as uid and gid uid_base + n (user niuu-slot-<n>); the "
        "range must be free on the VM image",
    )
    subnet: ipaddress.IPv4Network = Field(
        default=ipaddress.IPv4Network("10.249.0.0/16"),
        description="Private range the slots' networks are cut from, four addresses per "
        "slot; it bounds how many slots a VM can have and must not overlap the VM's own "
        "networks",
    )
    platform_forward_port_base: int = Field(
        default=18100,
        gt=0,
        le=65535,
        description="Slot n's Forge callback arrives on the VM's loopback at this port + n, "
        "where the slot's gateway forwards it",
    )
    image_user: str = Field(
        default="skuld",
        min_length=1,
        description="The Skuld image's session account, given the slot's uid in its containers",
    )
    cpu: float | None = Field(gt=0, description="CPU cores a slot may use, or null")
    memory_mib: int | None = Field(gt=0, description="Memory a slot may use, or null")
    pids: int | None = Field(gt=0, description="Processes and threads a slot may run, or null")

    @property
    def slot_capacity(self) -> int:
        return self.subnet.num_addresses // _ADDRESSES_PER_SLOT


def slot_index(lease) -> int:
    """The slot a runtime call is for: the lease view's slot, or 0 for a whole-VM lease."""
    slot = getattr(lease, "slot", 0)
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
        raise ValueError(f"A lease slot must be a non-negative integer, not {slot!r}")
    return slot


def state_key(lease) -> str:
    """The key of a lease's guest runtime state: its allocation, and its slot past 0.

    Slot 0 keeps the allocation's own key, so a VM with one session per machine
    names its tunnel, credential refresh and storage marker as before slots.
    """
    return _key(lease.id, slot_index(lease))


def _key(allocation_id: UUID, slot: int) -> str:
    return str(allocation_id) if slot == 0 else f"{allocation_id}-slot-{slot}"


@dataclass(frozen=True)
class SlotLayout:
    """Slot ``slot`` of allocation ``allocation_id`` under ``config``."""

    config: SlotIsolation
    allocation_id: UUID
    slot: int

    def __post_init__(self) -> None:
        if self.slot >= self.config.slot_capacity:
            raise ValueError(
                f"Slot {self.slot} does not fit slot_isolation.subnet {self.config.subnet} "
                f"({self.config.slot_capacity} slots); choose a larger subnet"
            )
        if self.uid > _MAX_ID:
            raise ValueError(f"Slot {self.slot} needs a uid above {_MAX_ID}; lower uid_base")
        if self.platform_forward_port > 65535:
            raise ValueError(
                f"Slot {self.slot}'s callback port {self.platform_forward_port} is not a TCP "
                "port; lower slot_isolation.platform_forward_port_base"
            )

    @classmethod
    def for_lease(cls, config: SlotIsolation, lease) -> SlotLayout:
        return cls(config=config, allocation_id=lease.id, slot=slot_index(lease))

    @property
    def name(self) -> str:
        return f"niuu-slot-{self.slot}"

    @property
    def uid(self) -> int:
        return self.config.uid_base + self.slot

    @property
    def key(self) -> str:
        return _key(self.allocation_id, self.slot)

    def _address(self, offset: int) -> str:
        network = self.config.subnet.network_address
        return str(network + self.slot * _ADDRESSES_PER_SLOT + offset)

    @property
    def gateway(self) -> str:
        return self._address(1)

    @property
    def address(self) -> str:
        return self._address(2)

    @property
    def platform_forward_port(self) -> int:
        return self.config.platform_forward_port_base + self.slot

    @property
    def data_root(self) -> str:
        return f"{SLOT_DATA_BASE}/{self.slot}"

    @property
    def run_root(self) -> str:
        return f"{SLOT_RUNTIME_BASE}/{self.slot}"

    @property
    def session_data(self) -> str:
        return f"{self.data_root}/session"

    @property
    def launch_path(self) -> str:
        return self.relocate(SESSION_LAUNCH)

    @property
    def holder(self) -> str:
        return f"{self.name}-holder"

    @property
    def skuld(self) -> str:
        return f"{self.name}-skuld"

    @property
    def slice(self) -> str:
        return f"niuu_slot_{self.slot}.slice"

    @property
    def host_link(self) -> str:
        return f"niuuslot{self.slot}"

    def _roots(self) -> tuple[tuple[str, str], ...]:
        etc = f"{self.data_root}/etc"
        return (
            (SESSION_DATA, self.session_data),
            (SESSION_LAUNCH, f"{etc}/session-launch.json"),
            (SESSION_SECRETS, f"{etc}/session-secrets"),
            (FORGE_SESSION_DIRECTORY, f"{etc}/forge-session"),
            (GUEST_AGENT_DIRECTORY, f"{etc}/guest-agent"),
            (SESSION_RUNTIME, self.run_root),
        )

    def relocate(self, path: str) -> str:
        """The slot's place for a session guest path; other guest paths are the VM's own."""
        candidate = PurePosixPath(path)
        for legacy, slot in self._roots():
            root = PurePosixPath(legacy)
            if candidate == root or root in candidate.parents:
                return str(PurePosixPath(slot) / candidate.relative_to(root))
        return path

    def supervisor_spec(self, *, image: str, platform_port: int) -> dict:
        """The slot as ``vm_slot_supervisor`` creates it."""
        return {
            "slot": self.slot,
            "allocation_id": str(self.allocation_id),
            "name": self.name,
            "uid": self.uid,
            "data_root": self.data_root,
            "run_root": self.run_root,
            "netns": self.name,
            "host_link": self.host_link,
            "gateway": self.gateway,
            "address": self.address,
            "prefix_length": 32 - (_ADDRESSES_PER_SLOT - 1).bit_length(),
            "platform_port": platform_port,
            "platform_forward_port": self.platform_forward_port,
            "slice": self.slice,
            "limits": {
                "cpu": self.config.cpu,
                "memory_mib": self.config.memory_mib,
                "pids": self.config.pids,
            },
            "holder": self.holder,
            "image": image,
            "image_user": self.config.image_user,
        }

    def launch(self, payload: dict, *, platform_port: int) -> dict:
        """A session's launch payload placed in this slot.

        Only guest-side sources move into the slot's directories: paths inside
        the containers stay where the session and its agents expect them. The
        broker listens on the slot's own address, which Forge's tunnel reaches,
        and calls Forge through the slot's gateway.
        """
        placed = copy.deepcopy(payload)
        for directory in placed.get("directories", []):
            directory["path"] = self.relocate(directory["path"])
        for mount in (*placed.get("secret_mounts", []), *placed.get("volume_mounts", [])):
            mount["source"] = self.relocate(mount["source"])
        for container in (*placed.get("init_containers", []), *placed.get("sidecars", [])):
            for mount in container["mounts"]:
                mount["source"] = self.relocate(mount["source"])
        tunnel = f"127.0.0.1:{platform_port}"
        environment = {
            key: value.replace(tunnel, f"{self.gateway}:{platform_port}")
            for key, value in placed["environment"].items()
        }
        environment["SKULD__HOST"] = "0.0.0.0"
        placed["environment"] = environment
        placed["slot"] = {
            "index": self.slot,
            "uid": self.uid,
            "data_root": self.session_data,
            "marker": self.key,
            "netns": self.name,
            "holder": self.holder,
            "skuld": self.skuld,
            "sidecar_prefix": f"{self.name}-sidecar-",
            "slice": self.slice,
            "labels": [f"{ALLOCATION_LABEL}={self.allocation_id}", f"{SLOT_LABEL}={self.slot}"],
            "identity": f"{self.data_root}/identity",
        }
        return placed

    def stop_spec(self) -> dict:
        """What the runtime's stop program needs to find this slot's containers."""
        return {
            "data_root": self.session_data,
            "marker": self.key,
            "skuld": self.skuld,
            "slot_label": f"{SLOT_LABEL}={self.slot}",
        }
