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

from niuu.utils import import_class, resolve_secret_kwargs

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
    except Exception:
        logger.warning("Configured transport could not be imported")
        return None

    try:
        return cls(**kwargs)
    except Exception:
        logger.warning("Configured transport could not be initialized")
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


def resolve_transport_kwargs(
    settings: Any,
    adapter: str,
    *,
    service_prefix: str,
) -> dict[str, Any]:
    """Resolve shared Sleipnir transport settings for Ravn and Skuld."""
    mesh = settings.mesh
    if adapter == "nng":
        from niuu.mesh.cluster import read_cluster_pub_addresses

        discovery_adapters = list(
            getattr(mesh, "discovery_adapters", None)
            or getattr(getattr(settings, "discovery", None), "adapters", [])
            or getattr(mesh, "adapters", [])
        )
        peer_id = getattr(mesh, "own_peer_id", "") or getattr(mesh, "peer_id", "")

        def _peer_addresses() -> list[str]:
            """Re-read the peer table on every re-discovery pass.

            A snapshot taken at build time left a node dialing only the peers
            that existed when it started, so anyone who joined later was
            unreachable in that direction.
            """
            return read_cluster_pub_addresses(discovery_adapters)

        return {
            "address": mesh.nng.pub_sub_address,
            "service_id": f"{service_prefix}:{peer_id}",
            "peer_addresses": _peer_addresses,
        }

    if adapter in ("sleipnir", "rabbitmq"):
        sleipnir = getattr(settings, "sleipnir", None)
        amqp_url_env = getattr(sleipnir, "amqp_url_env", "SLEIPNIR_AMQP_URL")
        amqp_url = resolve_secret_kwargs({}, {"amqp_url": amqp_url_env}).get("amqp_url", "")
        if not amqp_url:
            logger.warning("mesh: %s not set, rabbitmq transport unavailable", amqp_url_env)
            return {}
        return {"amqp_url": amqp_url}

    if adapter == "nats":
        nats = mesh.nats
        kwargs: dict[str, Any] = {
            "servers": list(nats.servers) or ["nats://localhost:4222"],
            "stream_name": nats.stream_name,
            "jetstream_domain": nats.jetstream_domain,
            "subject_prefix": nats.subject_prefix,
            "retention": nats.retention,
            "max_age_seconds": nats.max_age_seconds,
            "max_bytes": nats.max_bytes,
            "ring_buffer_depth": nats.ring_buffer_depth,
            "connect_timeout_s": nats.connect_timeout_s,
            "max_reconnect_attempts": nats.max_reconnect_attempts,
            "ensure_stream": nats.ensure_stream,
            "publish_timeout_s": nats.publish_timeout_s,
            "tls_ca_file": nats.tls_ca_file,
            "tls_ca_pem": nats.tls_ca_pem,
            "tls_cert_file": nats.tls_cert_file,
            "tls_key_file": nats.tls_key_file,
            "tls_hostname": nats.tls_hostname,
            "tls_handshake_first": nats.tls_handshake_first,
            "tls_legacy_ca": nats.tls_legacy_ca,
            "tls_insecure_skip_verify": nats.tls_insecure_skip_verify,
            "user": nats.user,
            "password": "",
            "token": "",
            "nkeys_seed_file": nats.nkeys_seed_file,
            "nkeys_seed": "",
            "extra_subscriptions": [
                {
                    "subject": entry.subject,
                    "stream_name": entry.stream_name,
                    "event_types": list(entry.event_types),
                }
                for entry in nats.extra_subscriptions
                if entry.subject
            ],
            "core_subscriptions": [
                {"subject": entry.subject} for entry in nats.core_subscriptions if entry.subject
            ],
        }
        if nats.consumer_group:
            kwargs["consumer_group"] = nats.consumer_group
        if nats.replay_from_sequence is not None:
            kwargs["replay_from_sequence"] = nats.replay_from_sequence
        return resolve_secret_kwargs(
            kwargs,
            {
                name: env_name
                for name, env_name in {
                    "user": nats.user_env,
                    "password": nats.password_env,
                    "token": nats.token_env,
                    "nkeys_seed": nats.nkeys_seed_env,
                }.items()
                if env_name
            },
        )

    if adapter == "redis":
        return resolve_secret_kwargs(
            {"redis_url": "redis://localhost:6379"},
            {"redis_url": getattr(mesh, "redis_url_env", "REDIS_URL")},
        )
    return {}
