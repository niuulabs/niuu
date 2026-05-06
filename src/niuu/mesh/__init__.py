"""Shared mesh plumbing for Skuld and Ravn (NIU-631).

This package centralises all mesh participation logic so both Skuld (CLI
broker) and Ravn (agent daemon) share a single implementation:

    niuu.mesh                  — transport builders, port allocation helpers
    niuu.mesh.cluster          — cluster.yaml peer address reader
    niuu.mesh.discovery_builder — discovery adapter construction
    niuu.mesh.identity         — MeshIdentity (shared peer identity model)
    niuu.mesh.participant      — MeshParticipant lifecycle wrapper
    niuu.mesh.transport_builder — Sleipnir transport construction

Public re-exports kept here for backward compatibility.
"""

from __future__ import annotations

import logging
import socket
from typing import Any, Protocol, runtime_checkable

from niuu.utils import import_class

logger = logging.getLogger("niuu.mesh")

MESH_ALIASES: dict[str, str] = {
    "sleipnir": "ravn.adapters.mesh.sleipnir_mesh.SleipnirMeshAdapter",
    "webhook": "ravn.adapters.mesh.webhook.WebhookMeshAdapter",
}


@runtime_checkable
class MeshConfigLike(Protocol):
    """Minimal mesh config interface shared by Ravn and Skuld settings."""

    @property
    def adapters(self) -> list[dict[str, Any]]: ...

    @property
    def rpc_timeout_s(self) -> float: ...


def build_mesh_from_adapters_list(
    adapters: list[dict[str, Any]],
    own_peer_id: str,
    rpc_timeout_s: float,
    *,
    discovery: Any | None = None,
    sleipnir_transport_builder: Any | None = None,
) -> Any:
    """Build mesh from a list of adapter entries (dynamic import pattern).

    Parameters
    ----------
    adapters:
        List of dicts, each with an "adapter" key (class path or alias)
        plus kwargs forwarded to the constructor.
    own_peer_id:
        This peer's identity for mesh routing.
    rpc_timeout_s:
        Default RPC timeout applied to each adapter.
    discovery:
        Optional discovery adapter passed to non-Sleipnir adapters.
    sleipnir_transport_builder:
        Optional callable(adapter_entry) -> (publisher, subscriber) for
        Sleipnir adapters that need transport injection. If None, Sleipnir
        adapters are instantiated with kwargs only.

    Returns
    -------
    A MeshPort implementation, or None if no adapters could be loaded.
    """
    CompositeMeshAdapter = import_class("ravn.adapters.mesh.composite.CompositeMeshAdapter")  # noqa: N806

    transports: list[Any] = []
    for entry in adapters:
        adapter_class = entry.get("adapter", "")
        if not adapter_class:
            logger.warning("mesh: adapter entry missing 'adapter' field, skipping")
            continue

        fq_class = MESH_ALIASES.get(adapter_class, adapter_class)

        try:
            cls = import_class(fq_class)
        except Exception as exc:
            logger.warning("mesh: failed to import %s: %s", fq_class, exc)
            continue

        kwargs = {k: v for k, v in entry.items() if k != "adapter"}
        kwargs["own_peer_id"] = own_peer_id
        kwargs["discovery"] = discovery
        kwargs.setdefault("rpc_timeout_s", rpc_timeout_s)

        # Sleipnir adapters need publisher/subscriber injection
        if "sleipnir" in fq_class.lower() and sleipnir_transport_builder is not None:
            transport = sleipnir_transport_builder(entry)
            if transport is None:
                logger.warning("mesh: failed to build Sleipnir transport, skipping")
                continue
            kwargs["publisher"] = transport
            kwargs["subscriber"] = transport
            kwargs.pop("discovery", None)

        try:
            transports.append(cls(**kwargs))
            logger.debug("mesh: loaded adapter %s", fq_class)
        except Exception as exc:
            logger.warning("mesh: failed to instantiate %s: %s", fq_class, exc)

    if not transports:
        logger.warning("mesh: no adapters loaded from config")
        return None

    if len(transports) == 1:
        return transports[0]

    return CompositeMeshAdapter(transports=transports, own_peer_id=own_peer_id)


def build_in_process_mesh(own_peer_id: str, rpc_timeout_s: float) -> Any:
    """Build a SleipnirMeshAdapter backed by InProcessBus (local/test mode)."""
    SleipnirMeshAdapter = import_class("ravn.adapters.mesh.sleipnir_mesh.SleipnirMeshAdapter")  # noqa: N806
    InProcessBus = import_class("sleipnir.adapters.in_process.InProcessBus")  # noqa: N806

    bus = InProcessBus()
    return SleipnirMeshAdapter(
        publisher=bus,
        subscriber=bus,
        own_peer_id=own_peer_id,
        rpc_timeout_s=rpc_timeout_s,
    )


def resolve_peer_id(configured_id: str) -> str:
    """Return *configured_id* if non-empty, else the machine hostname."""
    return configured_id or socket.gethostname()


def nng_ports_for(index: int, base_port: int) -> tuple[int, int, int]:
    """Return (pub_port, rep_port, handshake_port) for the nng node at *index*.

    Port allocation scheme (mirrors ravn flock init):
      pub       = base_port + index * 2
      rep       = base_port + index * 2 + 1
      handshake = base_port + 100 + index
    """
    pub = base_port + (index * 2)
    rep = base_port + (index * 2) + 1
    hs = base_port + 100 + index
    return pub, rep, hs


def nng_gateway_port_for(index: int, base_port: int) -> int:
    """Return the HTTP/WS gateway port for the nng node at *index*.

    gateway = base_port + 200 + index
    """
    return base_port + 200 + index
