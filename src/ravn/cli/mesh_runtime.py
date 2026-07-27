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
            environment_id=settings.discovery.realm_id,
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
        environment_id=settings.discovery.realm_id,
    )


def _resolve_transport_kwargs(
    settings: Settings,
    adapter: str,
) -> dict[str, Any]:
    """Build constructor kwargs for a Sleipnir transport from settings."""
    from niuu.mesh.transport_builder import resolve_transport_kwargs  # noqa: PLC0415

    return resolve_transport_kwargs(settings, adapter, service_prefix="ravn")


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


def _query_adapters_config(settings: Settings) -> list[dict[str, Any]]:
    """Return a discovery config safe to run alongside a live local node.

    ``ravn peers`` is a transient reader, not a participant: it dials peers to
    handshake but never needs to receive one. Reusing the node's configured
    handshake port would collide with the running daemon that already holds
    it, so mDNS entries are moved to an ephemeral port.
    """
    adapters: list[dict[str, Any]] = []
    for entry in getattr(settings.discovery, "adapters", []) or []:
        cfg = dict(entry)
        if "mdns" in str(cfg.get("adapter", "")).lower():
            cfg["handshake_port"] = 0
        adapters.append(cfg)
    return adapters


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
        adapters_config=_query_adapters_config(settings),
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
