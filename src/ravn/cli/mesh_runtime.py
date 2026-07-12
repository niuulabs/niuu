"""Focused runtime implementation extracted from :mod:`ravn.cli.commands`."""

from __future__ import annotations

# Dependencies are supplied by the CLI compatibility facade immediately before
# invocation so established command patch points remain effective.
# ruff: noqa: F821


def _build_mesh(settings: Settings, discovery: Any = None) -> Any:
    """Build mesh adapters using dynamic import from config.

    If settings.mesh.adapters is non-empty, uses the new list-based config.
    Otherwise falls back to legacy single-adapter mode for backward compatibility.

    All adapters run simultaneously via CompositeMeshAdapter:
    - publish() fans out to ALL transports
    - subscribe() registers on ALL transports
    - send() tries transports in order until success
    """
    import socket

    mesh_cfg = settings.mesh
    own_peer_id = mesh_cfg.own_peer_id or socket.gethostname()

    # New list-based config: delegate to shared niuu.mesh helper
    if mesh_cfg.adapters:
        from niuu.mesh import build_mesh_from_adapters_list  # noqa: PLC0415
        from niuu.mesh.transport_builder import build_transport  # noqa: PLC0415

        def _sleipnir_tb(entry: dict[str, Any]) -> Any:
            adapter = entry.get("transport", mesh_cfg.adapter or "nng")
            kwargs = _resolve_transport_kwargs(settings, adapter)
            if adapter in ("sleipnir", "rabbitmq") and not kwargs:
                return None
            return build_transport(adapter, **kwargs)

        return build_mesh_from_adapters_list(
            adapters=mesh_cfg.adapters,
            own_peer_id=own_peer_id,
            rpc_timeout_s=mesh_cfg.rpc_timeout_s,
            discovery=discovery,
            sleipnir_transport_builder=_sleipnir_tb,
        )

    # Legacy single-adapter mode for backward compatibility
    legacy_adapter = mesh_cfg.adapter or "nng"

    from niuu.mesh.transport_builder import build_transport  # noqa: PLC0415
    from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter  # noqa: PLC0415

    kwargs = _resolve_transport_kwargs(settings, legacy_adapter)
    if legacy_adapter in ("sleipnir", "rabbitmq") and not kwargs:
        logger.warning("mesh: failed to build transport, mesh disabled")
        return None

    transport = build_transport(legacy_adapter, **kwargs)
    if transport is None:
        logger.warning("mesh: failed to build transport, mesh disabled")
        return None

    return SleipnirMeshAdapter(
        publisher=transport,
        subscriber=transport,
        own_peer_id=own_peer_id,
        discovery=discovery,
        rpc_timeout_s=mesh_cfg.rpc_timeout_s,
    )


def _resolve_transport_kwargs(
    settings: Settings,
    adapter: str,
) -> dict[str, Any]:
    """Build constructor kwargs for a Sleipnir transport from settings."""
    if adapter == "nng":
        from niuu.mesh.cluster import read_cluster_pub_addresses  # noqa: PLC0415

        nng_cfg = settings.mesh.nng
        adapters_config = list(getattr(getattr(settings, "discovery", None), "adapters", []))
        peer_addresses = read_cluster_pub_addresses(adapters_config)
        return {
            "address": nng_cfg.pub_sub_address,
            "service_id": f"ravn:{settings.mesh.own_peer_id}",
            "peer_addresses": peer_addresses or None,
        }

    if adapter in ("sleipnir", "rabbitmq"):
        amqp_url = os.environ.get(settings.sleipnir.amqp_url_env, "")
        if not amqp_url:
            logger.warning(
                "mesh: %s not set, rabbitmq transport unavailable",
                settings.sleipnir.amqp_url_env,
            )
            return {}
        return {"amqp_url": amqp_url}

    if adapter == "nats":
        nats_cfg = settings.mesh.nats
        servers = list(nats_cfg.servers)
        kwargs: dict[str, Any] = {
            "servers": servers or ["nats://localhost:4222"],
            "stream_name": nats_cfg.stream_name,
            "jetstream_domain": nats_cfg.jetstream_domain,
            "subject_prefix": nats_cfg.subject_prefix,
            "retention": nats_cfg.retention,
            "max_age_seconds": nats_cfg.max_age_seconds,
            "max_bytes": nats_cfg.max_bytes,
            "ring_buffer_depth": nats_cfg.ring_buffer_depth,
            "connect_timeout_s": nats_cfg.connect_timeout_s,
            "max_reconnect_attempts": nats_cfg.max_reconnect_attempts,
            "ensure_stream": nats_cfg.ensure_stream,
            "publish_timeout_s": nats_cfg.publish_timeout_s,
            "tls_ca_file": nats_cfg.tls_ca_file,
            "tls_ca_pem": nats_cfg.tls_ca_pem,
            "tls_cert_file": nats_cfg.tls_cert_file,
            "tls_key_file": nats_cfg.tls_key_file,
            "tls_hostname": nats_cfg.tls_hostname,
            "tls_handshake_first": nats_cfg.tls_handshake_first,
            "tls_insecure_skip_verify": nats_cfg.tls_insecure_skip_verify,
            "user": os.environ.get(nats_cfg.user_env, nats_cfg.user)
            if nats_cfg.user_env
            else nats_cfg.user,
            "password": os.environ.get(nats_cfg.password_env, "") if nats_cfg.password_env else "",
            "token": os.environ.get(nats_cfg.token_env, "") if nats_cfg.token_env else "",
            "nkeys_seed_file": nats_cfg.nkeys_seed_file,
            "nkeys_seed": os.environ.get(nats_cfg.nkeys_seed_env, "")
            if nats_cfg.nkeys_seed_env
            else "",
            "extra_subscriptions": [
                {
                    "subject": entry.subject,
                    "stream_name": entry.stream_name,
                    "event_types": list(entry.event_types),
                }
                for entry in nats_cfg.extra_subscriptions
                if entry.subject
            ],
            "core_subscriptions": [
                {"subject": entry.subject} for entry in nats_cfg.core_subscriptions if entry.subject
            ],
        }
        if nats_cfg.consumer_group:
            kwargs["consumer_group"] = nats_cfg.consumer_group
        if nats_cfg.replay_from_sequence is not None:
            kwargs["replay_from_sequence"] = nats_cfg.replay_from_sequence
        return kwargs

    if adapter == "redis":
        redis_url = os.environ.get(settings.mesh.redis_url_env, "redis://localhost:6379")
        return {"redis_url": redis_url}

    return {}


def _build_discovery(
    settings: Settings,
    persona_config: Any | None = None,
    profile_name: str = "default",
) -> Any:
    """Build the discovery adapter from config, wiring the own identity."""
    import importlib.metadata

    from ravn.adapters.discovery._identity import (
        load_or_create_peer_id,
        load_or_create_realm_key,
        realm_id_from_key,
    )
    from ravn.domain.models import RavnIdentity

    peer_id = settings.mesh.own_peer_id or load_or_create_peer_id()
    realm_key = load_or_create_realm_key()
    realm_id = settings.discovery.realm_id or realm_id_from_key(realm_key)

    try:
        version = importlib.metadata.version("ravn")
    except Exception:
        version = "0.0.0"

    # Advertise addresses that remote peers can connect to.
    # Replace nng wildcard listen address (*) with 127.0.0.1 for same-host meshes.
    rep_address = settings.mesh.nng.req_rep_address.replace("*", "127.0.0.1")
    pub_address = settings.mesh.nng.pub_sub_address.replace("*", "127.0.0.1")

    persona_name = (
        getattr(persona_config, "name", None) or settings.agent.system_prompt[:30] or "ravn"
    )
    capabilities = _derive_capabilities(settings, persona_config, profile_name)

    # Extract event types this persona consumes (for mesh routing)
    consumes_event_types: list[str] = []
    if persona_config is not None and hasattr(persona_config, "consumes"):
        consumes_event_types = list(persona_config.consumes.event_types or [])

    identity = RavnIdentity(
        peer_id=peer_id,
        realm_id=realm_id,
        persona=persona_name,
        capabilities=capabilities,
        permission_mode=settings.permission.mode,
        version=version,
        consumes_event_types=consumes_event_types,
        rep_address=rep_address,
        pub_address=pub_address,
    )

    from niuu.mesh.discovery_builder import build_discovery_adapters  # noqa: PLC0415
    from niuu.mesh.transport_builder import build_transport  # noqa: PLC0415

    def _event_bus_transport(entry: dict[str, Any]) -> Any:
        transport_name = str(entry.get("transport") or "nats")
        kwargs = _resolve_transport_kwargs(settings, transport_name)
        return build_transport(transport_name, **kwargs) if kwargs else None

    return build_discovery_adapters(
        adapters_config=list(getattr(settings.discovery, "adapters", [])),
        own_identity=identity,
        heartbeat_interval_s=settings.discovery.heartbeat_interval_s,
        peer_ttl_s=settings.discovery.peer_ttl_s,
        sleipnir_transport_builder=_event_bus_transport,
    )


async def _run_peers(settings: Settings, *, verbose: bool, force_scan: bool) -> None:
    """Build a discovery adapter, optionally scan, and print the peer table."""
    from ravn.adapters.discovery._identity import (
        load_or_create_peer_id,
        load_or_create_realm_key,
        realm_id_from_key,
    )
    from ravn.domain.models import RavnIdentity

    peer_id = load_or_create_peer_id()
    realm_key = load_or_create_realm_key()
    realm_id = realm_id_from_key(realm_key)

    import importlib.metadata

    try:
        version = importlib.metadata.version("ravn")
    except Exception:
        version = "0.0.0"

    identity = RavnIdentity(
        peer_id=peer_id,
        realm_id=realm_id,
        persona=settings.agent.system_prompt[:30] if settings.agent.system_prompt else "ravn",
        capabilities=[],
        permission_mode=settings.permission.mode,
        version=version,
    )

    from niuu.mesh.discovery_builder import build_discovery_adapters  # noqa: PLC0415

    discovery = build_discovery_adapters(
        adapters_config=list(getattr(settings.discovery, "adapters", [])),
        own_identity=identity,
        heartbeat_interval_s=settings.discovery.heartbeat_interval_s,
        peer_ttl_s=settings.discovery.peer_ttl_s,
    )
    if discovery is None:
        typer.echo("No discovery adapter configured.", err=True)
        return

    await discovery.start()

    # Wait for mDNS announcements from peers to arrive before querying the table.
    convergence_wait = getattr(settings.discovery.mdns, "convergence_wait_s", 3.0)
    await asyncio.sleep(convergence_wait)

    if force_scan:
        candidates = await discovery.scan()
        typer.echo(f"Scan found {len(candidates)} candidate(s).")
        for c in candidates:
            if c.peer_id not in discovery.peers():
                peer = await discovery.handshake(c)
                if peer is not None:
                    typer.echo(f"  Handshook with {c.peer_id}")

    verified = discovery.peers()
    if not verified:
        typer.echo("No verified flock members found.")
        await discovery.stop()
        return

    typer.echo(f"Flock members ({len(verified)}):")
    for pid, peer in sorted(verified.items()):
        caps = ", ".join(peer.capabilities) if peer.capabilities else "—"
        line = f"  {pid:<20}  {peer.persona:<20} [{peer.status}]  caps={caps}"
        if verbose:
            rep = peer.rep_address or "—"
            pub = peer.pub_address or "—"
            latency = f"{peer.latency_ms:.1f}ms" if peer.latency_ms is not None else "—"
            line += f"\n    rep={rep}  pub={pub}  latency={latency}  tasks={peer.task_count}"
            line += f"  last_seen={peer.last_seen.isoformat()}"
        typer.echo(line)

    await discovery.stop()
