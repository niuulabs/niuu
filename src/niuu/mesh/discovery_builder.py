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
}


def build_discovery_adapters(
    adapters_config: list[dict[str, Any]],
    own_identity: Any,
    *,
    heartbeat_interval_s: float = 5.0,
    peer_ttl_s: float = 30.0,
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

    Returns
    -------
    A ``DiscoveryPort`` implementation, or ``None`` if no adapters loaded.
    """
    if not adapters_config:
        return None

    CompositeDiscoveryAdapter = import_class(  # noqa: N806
        "ravn.adapters.discovery.composite.CompositeDiscoveryAdapter"
    )

    backends: list[Any] = []

    for entry in adapters_config:
        adapter_class = entry.get("adapter", "")
        if not adapter_class:
            logger.warning("discovery: adapter entry missing 'adapter' field, skipping")
            continue

        fq_class = DISCOVERY_ALIASES.get(adapter_class, adapter_class)

        try:
            cls = import_class(fq_class)
        except Exception as exc:
            logger.warning("discovery: failed to import %s: %s", fq_class, exc)
            continue

        kwargs = {k: v for k, v in entry.items() if k != "adapter"}
        kwargs["own_identity"] = own_identity
        kwargs.setdefault("heartbeat_interval_s", heartbeat_interval_s)
        kwargs.setdefault("peer_ttl_s", peer_ttl_s)

        try:
            backend = cls(**kwargs)
            backends.append(backend)
            logger.debug("discovery: loaded adapter %s", fq_class)
        except Exception as exc:
            logger.warning("discovery: failed to instantiate %s: %s", fq_class, exc)

    if not backends:
        logger.warning("discovery: no adapters loaded from config")
        return None

    if len(backends) == 1:
        return backends[0]

    return CompositeDiscoveryAdapter(backends=backends)
