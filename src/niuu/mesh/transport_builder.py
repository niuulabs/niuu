"""Shared Sleipnir transport builder (NIU-631).

Extracted from ``ravn.cli.commands._build_sleipnir_transport`` so both Ravn
and Skuld can build NNG / RabbitMQ / NATS / Redis transports without
duplicating the import + instantiate pattern.

Callers are responsible for resolving transport kwargs from their own
settings objects — this module only handles the alias resolution, import, and
instantiation steps.
"""

from __future__ import annotations

import logging
from typing import Any

from niuu.utils import import_class

logger = logging.getLogger("niuu.mesh.transport")

TRANSPORT_ALIASES: dict[str, str] = {
    "nng": "sleipnir.adapters.nng_transport.NngTransport",
    "sleipnir": "sleipnir.adapters.rabbitmq.RabbitMQTransport",
    "rabbitmq": "sleipnir.adapters.rabbitmq.RabbitMQTransport",
    "nats": "sleipnir.adapters.nats_transport.NatsTransport",
    "redis": "sleipnir.adapters.redis_streams.RedisStreamsTransport",
    "in_process": "sleipnir.adapters.in_process.InProcessBus",
}


def build_transport(adapter: str, **kwargs: Any) -> Any | None:
    """Import and instantiate a Sleipnir transport adapter.

    Parameters
    ----------
    adapter:
        Short name (e.g. ``"nng"``, ``"rabbitmq"``) or fully-qualified class
        path.  Short names are resolved via :data:`TRANSPORT_ALIASES`.
    **kwargs:
        Constructor arguments forwarded to the transport class.

    Returns
    -------
    The transport instance, or ``None`` if import or instantiation fails.
    """
    fq_class = TRANSPORT_ALIASES.get(adapter, adapter)

    try:
        cls = import_class(fq_class)
    except Exception as exc:
        logger.warning("transport: failed to import %s: %s", fq_class, exc)
        return None

    try:
        return cls(**kwargs)
    except Exception as exc:
        logger.warning("transport: failed to instantiate %s: %s", fq_class, exc)
        return None


def build_nng_transport(
    address: str,
    service_id: str,
    peer_addresses: list[str] | None = None,
) -> Any | None:
    """Build an NNG pub/sub transport.

    Convenience wrapper around :func:`build_transport` for the common NNG case.

    Parameters
    ----------
    address:
        NNG pub/sub bind address (e.g. ``"tcp://127.0.0.1:6000"``).
    service_id:
        Logical identifier for this node (used in NNG headers).
    peer_addresses:
        Remote pub addresses to dial on startup.  ``None`` means no peers.
    """
    return build_transport(
        "nng",
        address=address,
        service_id=service_id,
        peer_addresses=peer_addresses or None,
    )
