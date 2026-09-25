"""Shared discovery adapter builder (NIU-631).

Extracted from ``ravn.cli.commands._build_discovery_adapters`` so both Ravn
and Skuld can construct discovery adapters from config without duplicating the
dynamic import + composite wiring pattern.

Callers supply a pre-built ``own_identity`` object (either
``ravn.domain.models.RavnIdentity`` or ``niuu.mesh.identity.MeshIdentity`` —
discovery adapters use duck typing and only access named fields).
"""

from __future__ import annotations

import logging
from typing import Any

from niuu.utils import import_class

logger = logging.getLogger("niuu.mesh.discovery")

DISCOVERY_ALIASES: dict[str, str] = {
    "mdns": "ravn.adapters.discovery.mdns.MdnsDiscoveryAdapter",
    "sleipnir": "ravn.adapters.discovery.sleipnir.SleipnirDiscoveryAdapter",
    "k8s": "ravn.adapters.discovery.k8s.K8sDiscoveryAdapter",
    "static": "ravn.adapters.discovery.static.StaticDiscoveryAdapter",
    "event_bus": "ravn.adapters.discovery.event_bus.EventBusDiscoveryAdapter",
}


class DiscoveryBuildError(RuntimeError):
    """A configured discovery adapter entry cannot be imported or constructed."""


def build_discovery_adapters(
    adapters_config: list[dict[str, Any]],
    own_identity: Any,
    *,
    heartbeat_interval_s: float = 5.0,
    peer_ttl_s: float = 30.0,
    sleipnir_transport_builder: Any | None = None,
) -> Any | None:
    """Build discovery adapters from a list-based config using dynamic import.

    All adapters run simultaneously via ``CompositeDiscoveryAdapter``.  When
    only one adapter is configured the composite is skipped and the single
    adapter is returned directly.

    Parameters
    ----------
    adapters_config:
        List of dicts, each with an ``"adapter"`` key (class path or alias)
        plus additional kwargs forwarded to the constructor.
    own_identity:
        This node's identity — passed as ``own_identity`` kwarg to each
        discovery adapter.  Accepts any object with the expected fields
        (``RavnIdentity`` or ``MeshIdentity``).
    heartbeat_interval_s:
        Default heartbeat interval injected when not specified per-adapter.
    peer_ttl_s:
        Default peer TTL injected when not specified per-adapter.
    sleipnir_transport_builder:
        Optional callable(adapter_entry) -> transport (publisher and
        subscriber) for adapters that declare ``requires_sleipnir_transport``.

    Returns
    -------
    A ``DiscoveryPort`` implementation, or ``None`` when *adapters_config* is
    empty (no discovery configured).

    Raises
    ------
    DiscoveryBuildError
        An entry has no ``adapter`` key, its class cannot be imported or
        constructed, or it needs a Sleipnir transport and no
        *sleipnir_transport_builder* was given. A configured adapter that
        cannot be built is fatal, never skipped.
    TransportBuildError
        *sleipnir_transport_builder* could not build an entry's transport.
    """
    if not adapters_config:
        return None

    CompositeDiscoveryAdapter = import_class(  # noqa: N806
        "ravn.adapters.discovery.composite.CompositeDiscoveryAdapter"
    )

    backends: list[Any] = []

    for index, entry in enumerate(adapters_config):
        adapter_class = entry.get("adapter", "")
        if not adapter_class:
            raise DiscoveryBuildError(
                f"discovery adapter entry {index} has no 'adapter' key: set it to one of "
                f"{', '.join(sorted(DISCOVERY_ALIASES))} or a fully-qualified class path"
            )

        fq_class = DISCOVERY_ALIASES.get(adapter_class, adapter_class)
        if "." not in fq_class:
            raise DiscoveryBuildError(
                f"unknown discovery adapter {adapter_class!r} in entry {index}: set it to one "
                f"of {', '.join(sorted(DISCOVERY_ALIASES))} or a fully-qualified class path"
            )

        try:
            cls = import_class(fq_class)
        except (ImportError, AttributeError) as exc:
            raise DiscoveryBuildError(
                f"discovery adapter {adapter_class!r} in entry {index} could not be imported "
                f"from {fq_class}: {exc}. Install the package that provides it, "
                "or remove the entry"
            ) from exc

        kwargs = {k: v for k, v in entry.items() if k != "adapter"}
        kwargs["own_identity"] = own_identity
        kwargs.setdefault("heartbeat_interval_s", heartbeat_interval_s)
        kwargs.setdefault("peer_ttl_s", peer_ttl_s)
        if getattr(cls, "requires_sleipnir_transport", False):
            if sleipnir_transport_builder is None:
                raise DiscoveryBuildError(
                    f"discovery adapter {adapter_class!r} in entry {index} needs a Sleipnir "
                    "transport and none is available on this path; it cannot run here. "
                    "Remove the entry, or use a discovery adapter that needs no transport"
                )
            transport = sleipnir_transport_builder(entry)
            kwargs["publisher"] = transport
            kwargs["subscriber"] = transport
            kwargs.pop("transport", None)

        try:
            backends.append(cls(**kwargs))
        except Exception as exc:
            raise DiscoveryBuildError(
                f"discovery adapter {adapter_class!r} in entry {index} ({fq_class}) could not "
                f"be constructed: {exc}. Fix that entry's settings"
            ) from exc
        logger.debug("discovery: loaded adapter %s", fq_class)

    if len(backends) == 1:
        return backends[0]

    return CompositeDiscoveryAdapter(backends=backends)
