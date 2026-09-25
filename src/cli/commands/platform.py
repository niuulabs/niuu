"""Platform lifecycle commands: up, down, status, init.

``niuu platform up`` dynamically builds --<service>/--no-<service> flags
from all registered ServiceDefinitions.  Future plugins add their flags
automatically — no code changes needed here.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from cli.services.manager import ServiceState, StartupError
from cli.services.preflight import (
    PreflightConfig,
    format_results,
    has_failures,
    run_preflight_checks,
)

if TYPE_CHECKING:
    from bifrost.config import BifrostConfig
    from cli.config import CLISettings
    from cli.registry import PluginRegistry
    from cli.services.manager import ServiceManager
    from niuu.ports.plugin import ServiceDefinition


def _build_preflight_config(
    settings: CLISettings,
    *,
    workspaces_dir_override: str = "",
) -> PreflightConfig:
    """Build a PreflightConfig from CLISettings."""
    ports = [settings.server.port]
    for plugin_cfg in settings.plugins.extra:
        plugin_port = plugin_cfg.get("port")
        if isinstance(plugin_port, int) and plugin_port not in ports:
            ports.append(plugin_port)

    kwargs = settings.pod_manager.adapter_kwargs()
    workspaces_dir = workspaces_dir_override or kwargs.get("workspaces_dir", "~/.niuu/workspaces")
    return PreflightConfig(
        claude_binary=kwargs.get("claude_binary", "claude"),
        ports=ports,
        workspaces_dir=workspaces_dir,
        database_mode=settings.database.mode,
        database_dsn=settings.database.dsn,
        mode=settings.mode,
        kubeconfig=kwargs.get("kubeconfig", "~/.kube/config"),
        namespace=kwargs.get("namespace", "volundr"),
    )


def _collect_service_definitions(
    registry: PluginRegistry,
) -> dict[str, ServiceDefinition]:
    """Collect ServiceDefinitions from all registered plugins."""
    defs: dict[str, ServiceDefinition] = {}
    for plugin in registry.all_plugins.values():
        svc_def = plugin.register_service()
        if svc_def is not None:
            defs[svc_def.name] = svc_def
    return defs


def _resolve_enabled_services(
    service_defs: dict[str, ServiceDefinition],
    settings: CLISettings,
    start_all: bool,
    svc_flags: dict[str, bool | None],
) -> set[str]:
    """Compute which services to start from defaults, config, and CLI flags.

    Resolution order (highest priority last wins):
    1. Plugin default_enabled
    2. settings.service_overrides
    3. CLI --<service>/--no-<service> flags
    4. CLI --all flag (overrides everything — starts all services)
    """
    if start_all:
        return set(service_defs.keys())

    enabled: set[str] = set()

    for svc_name, svc_def in service_defs.items():
        # Start from plugin default
        is_enabled = svc_def.default_enabled

        # Apply config override if present
        override = settings.service_overrides.get(svc_name)
        if override is not None and override.enabled is not None:
            is_enabled = override.enabled

        if is_enabled:
            enabled.add(svc_name)

    # Apply CLI flag overrides
    for svc_name, flag_val in svc_flags.items():
        if flag_val is True:
            enabled.add(svc_name)
        elif flag_val is False:
            enabled.discard(svc_name)
        # None means "not specified" — keep config/default value

    return enabled


def _print_status(name: str, state: ServiceState) -> None:
    """Print service status changes to the terminal."""
    match state:
        case ServiceState.STARTING:
            typer.echo(f"  Starting {name}...", nl=False)
        case ServiceState.HOSTED:
            typer.echo(f"  Hosted {name} by root server")
        case ServiceState.HEALTHY:
            typer.echo(" ok")
        case ServiceState.UNHEALTHY:
            typer.echo(" FAILED")
        case ServiceState.STOPPING:
            typer.echo(f"  Stopping {name}...", nl=False)
        case ServiceState.STOPPED:
            typer.echo(" done")


async def _startup(
    manager: ServiceManager,
    settings: CLISettings,
    enabled_services: set[str] | None,
    skip_preflight: bool,
    host_profile: str,
    enabled_mounts: set[str] | None,
    workspaces_dir_override: str = "",
) -> None:
    """Run preflight checks, start infrastructure, then the root server."""
    if not skip_preflight:
        typer.echo("Running preflight checks...")
        config = _build_preflight_config(
            settings,
            workspaces_dir_override=workspaces_dir_override,
        )
        results = run_preflight_checks(config)
        typer.echo(format_results(results))

        if has_failures(results):
            typer.echo("\nPreflight checks failed. Fix the issues above and retry.")
            raise typer.Exit(1)
        typer.echo()

    typer.echo("Starting services...")
    try:
        await manager.start_all(enabled_services=enabled_services, rollback_on_failure=True)
    except StartupError as exc:
        typer.echo(f"\nStartup failed: {exc}")
        raise typer.Exit(1) from None

    # Start the unified root server (all plugin APIs + web UI on one port)
    from niuu.app import RootServer

    host = settings.server.host
    port = settings.server.port

    public_host = settings.server.external_host.strip() or host

    # Expose bind/public server addresses so browser-facing URLs can differ
    # from the interface uvicorn listens on.
    os.environ["NIUU_SERVER_HOST"] = host
    os.environ["NIUU_SERVER_PUBLIC_HOST"] = public_host
    os.environ["NIUU_SERVER_PORT"] = str(port)
    os.environ["NIUU_DATABASE_MODE"] = settings.database.mode

    root_server = RootServer(
        registry=manager._registry,
        host=host,
        public_host=public_host,
        port=port,
        host_profile=host_profile,
        enabled_mounts=enabled_mounts,
        # Browser-asserted dev identity (client x-auth-*, devUserId/devRoles
        # query params) is only the identity contract for a mini host that
        # ALSO explicitly declared it has no authentication (auth.mode:
        # none). auth.mode: oidc verifies bearer tokens itself — trusting
        # the browser's own claims there would silently undo that.
        dev_identity=settings.mode == "mini" and settings.host_auth.mode == "none",
        cli_settings=settings,
    )
    manager._root_server = root_server  # type: ignore[attr-defined]

    typer.echo("  Starting API server...", nl=False)
    await root_server.start()

    # Wait for the server to become healthy
    for _ in range(15):
        if await root_server.health_check():
            break
        await asyncio.sleep(0.5)

    if await root_server.health_check():
        typer.echo(" ok")
    else:
        typer.echo(" FAILED")
        raise typer.Exit(1)

    typer.echo(f"\nReady! Platform running on http://{host}:{port}")
    typer.echo(f"  API:    http://{host}:{port}/api/v1/")
    typer.echo(f"  Web UI: http://{host}:{port}/")


async def _shutdown(manager: ServiceManager) -> None:
    """Stop all services gracefully."""
    typer.echo("Stopping services...")
    root_server = getattr(manager, "_root_server", None)
    if root_server:
        typer.echo("  Stopping API server...", nl=False)
        await root_server.stop()
        typer.echo(" done")
    await manager.stop_all()


def _build_up_callback(
    service_defs: dict[str, ServiceDefinition],
    manager: ServiceManager,
    settings: CLISettings,
) -> object:
    """Build the ``up`` command function with dynamic service flag parameters.

    For each ServiceDefinition, an ``Optional[bool]`` parameter is injected
    so Typer generates ``--<name>/--no-<name>`` flag pairs.  A value of None
    means "not specified on the CLI" and defers to config/default.
    """

    def up(**kwargs: bool | None) -> None:
        """Start platform services."""
        from niuu.app import DEFAULT_HOST_PROFILE, parse_enabled_mounts

        workspaces_dir = str(kwargs.pop("workspaces_dir", "") or "").strip()
        effective_settings = settings
        if workspaces_dir:
            if settings.mode != "mini":
                raise typer.BadParameter(
                    "--workspaces-dir is only supported in mini mode",
                    param_hint="workspaces-dir",
                )
            effective_settings = settings.model_copy(deep=True)
            effective_settings.pod_manager.workspaces_dir = workspaces_dir

        skip_preflight: bool = bool(kwargs.pop("skip_preflight", False))
        start_all: bool = bool(kwargs.pop("all", False))
        no_web: bool = bool(kwargs.pop("no_web", False))
        host_profile = str(kwargs.pop("host_profile", DEFAULT_HOST_PROFILE))
        mounts = str(kwargs.pop("mounts", ""))
        svc_flags: dict[str, bool | None] = dict(kwargs)

        try:
            enabled_mounts = parse_enabled_mounts(mounts)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="mounts") from exc

        if effective_settings.mode == "docker":
            from cli.commands.stack import stack_up

            stack_up(effective_settings, skip_preflight=skip_preflight)
            return

        enabled = _resolve_enabled_services(service_defs, settings, start_all, svc_flags)
        environment_before: dict[str, str | None] = {}

        def _set_environment(key: str, value: str, *, only_if_missing: bool = False) -> None:
            if key not in environment_before:
                environment_before[key] = os.environ.get(key)
            if not only_if_missing or key not in os.environ:
                os.environ[key] = value

        # Host-local runtime needs local mount support and dynamic PodManager env.
        if effective_settings.mode == "mini":
            _set_environment("LOCAL_MOUNTS__ENABLED", "true", only_if_missing=True)
            _set_environment("LOCAL_MOUNTS__MINI_MODE", "true", only_if_missing=True)
            local_env = _resolve_local_pod_manager_env(effective_settings)
            _check_auth_env_conflicts(local_env, effective_settings.host_auth.mode)
            for key, value in local_env.items():
                _set_environment(
                    key,
                    value,
                    only_if_missing=key in {"RESIDENT_RUNTIMES", "CREDENTIAL_STORE"},
                )

        if no_web:
            _set_environment("NIUU_NO_WEB", "true")

        async def _run() -> None:
            await _startup(
                manager,
                effective_settings,
                enabled,
                skip_preflight,
                host_profile,
                enabled_mounts,
                workspaces_dir_override=workspaces_dir,
            )

            # Wait forever until cancelled by KeyboardInterrupt
            with suppress(asyncio.CancelledError):
                await asyncio.Event().wait()

            await _shutdown(manager)

        try:
            asyncio.run(_run())
        except KeyboardInterrupt:
            typer.echo("\nReceived shutdown signal...")
            asyncio.run(_shutdown(manager))
        finally:
            for key, previous in environment_before.items():
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous
        typer.echo("All services stopped. Goodbye.")

    # Build dynamic signature: skip_preflight, all, then one bool | None per service.
    # Typer reads __signature__ and __annotations__ for introspection, and takes
    # each option's help from the typer.Option used as the parameter default.
    # Flag names are left to Typer so they keep deriving from the parameter name.
    # "all" is a valid inspect.Parameter name even though it shadows the builtin.
    params = [
        inspect.Parameter(
            "skip_preflight",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=typer.Option(
                False,
                help="Start without running the host preflight checks.",
            ),
            annotation=bool,
        ),
        inspect.Parameter(
            "all",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=typer.Option(
                False,
                help="Start every registered service, ignoring per-service defaults.",
            ),
            annotation=bool,
        ),
        inspect.Parameter(
            "no_web",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=typer.Option(
                False,
                help="Start the backend services without serving the web UI.",
            ),
            annotation=bool,
        ),
        inspect.Parameter(
            "host_profile",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=typer.Option(
                "full",
                help="Host profile that decides which route domains are mounted.",
            ),
            annotation=str,
        ),
        inspect.Parameter(
            "mounts",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=typer.Option(
                "",
                help=("Comma-separated route domains to mount instead of the profile default."),
            ),
            annotation=str,
        ),
        inspect.Parameter(
            "workspaces_dir",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=typer.Option(
                "",
                help="Directory for session workspaces. Mini mode only.",
            ),
            annotation=str,
        ),
    ]
    for svc_name in sorted(service_defs.keys()):
        description = (service_defs[svc_name].description or "").strip().rstrip(".")
        detail = f" — {description}" if description else ""
        params.append(
            inspect.Parameter(
                svc_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=typer.Option(
                    None,
                    help=f"Force the {svc_name} service on or off{detail}.",
                ),
                annotation=bool | None,
            )
        )

    up.__signature__ = inspect.Signature(params)
    up.__annotations__ = {p.name: p.annotation for p in params}
    up.__name__ = "up"
    up.__doc__ = "Start platform services (use --all to start everything)."
    return up


#: Env vars auth_adapter_env() computes that select WHICH identity/authz
#: adapter runs. An operator-set value here disagreeing with what auth.mode
#: computes must raise, not silently be overwritten — the running host would
#: then not match what config.yaml or /auth/config claims. BIFROST_CONFIG is
#: checked separately below: it is a full BifrostConfig JSON blob (providers,
#: models, ...), not an adapter-selection string, so comparing it whole here
#: would raise over unrelated provider/model differences and would blame
#: auth.mode for them — only its own auth_mode field is this function's
#: concern, and only under 'oidc' (host_auth.mode: none never touches
#: bifrost.auth_mode at all — see cli.commands.platform._effective_bifrost_config).
_AUTH_ADAPTER_ENV_KEYS = frozenset(
    {
        "IDENTITY__ADAPTER",
        "AUTHORIZATION__ADAPTER",
        "RAVN_API_AUTH__ADAPTER",
        "AUTH__ADAPTER",
        "HOST_IDENTITY__ADAPTER",
        "MIMIR_AUTH__ADAPTER",
        "AUTH_MODE",
    }
)


def _check_bifrost_config_auth_mode_conflict(computed: dict[str, str], auth_mode: str) -> None:
    """Raise if the operator's own BIFROST_CONFIG disagrees on auth_mode only.

    Only meaningful under 'oidc': that is the only mode
    ``_effective_bifrost_config`` forces ``bifrost.auth_mode`` to a specific
    value, so it is the only mode where an operator-set BIFROST_CONFIG could
    silently lose that override. Comparing the full JSON (as the other
    _AUTH_ADAPTER_ENV_KEYS do) would also raise over incidental differences
    in providers/models that have nothing to do with auth.mode.
    """
    if auth_mode != "oidc":
        return
    existing_raw = os.environ.get("BIFROST_CONFIG")
    computed_raw = computed.get("BIFROST_CONFIG")
    if existing_raw is None or computed_raw is None:
        return
    try:
        existing_auth_mode = json.loads(existing_raw).get("auth_mode")
        computed_auth_mode = json.loads(computed_raw).get("auth_mode")
    except (ValueError, AttributeError):
        return
    if existing_auth_mode is None or existing_auth_mode == computed_auth_mode:
        return
    raise typer.BadParameter(
        f"BIFROST_CONFIG is already set in the environment with auth_mode "
        f"{existing_auth_mode!r}, which disagrees with auth.mode: {auth_mode!r} "
        f"(computed {computed_auth_mode!r}). Remove the operator-set BIFROST_CONFIG "
        "env var, or change auth.mode/host_auth.oidc to match it.",
        param_hint="BIFROST_CONFIG",
    )


def _check_auth_env_conflicts(computed: dict[str, str], auth_mode: str) -> None:
    """Raise if the operator's own environment disagrees with auth.mode.

    Selecting the identity/authorization adapter used to unconditionally
    overwrite whatever the operator had already exported — silently
    discarding it. The running host would then not match what config.yaml
    or /auth/config claims, which is exactly the "claims a security the
    host doesn't have" failure mode this whole fix round exists to close.
    """
    for key in _AUTH_ADAPTER_ENV_KEYS:
        existing = os.environ.get(key)
        computed_value = computed.get(key)
        if existing is None or computed_value is None or existing == computed_value:
            continue
        raise typer.BadParameter(
            f"{key} is already set in the environment to {existing!r}, which "
            f"disagrees with auth.mode: {auth_mode!r} (computed {computed_value!r}). "
            f"Remove the operator-set {key} env var, or change auth.mode/"
            "host_auth.oidc to match it.",
            param_hint=key,
        )
    _check_bifrost_config_auth_mode_conflict(computed, auth_mode)


MINI_POD_MANAGER_ADAPTER = "volundr.adapters.outbound.local_process.LocalProcessPodManager"
CLUSTER_POD_MANAGER_ADAPTER = "volundr.adapters.outbound.direct_k8s_pod_manager.DirectK8sPodManager"
OPENSHELL_POD_MANAGER_ADAPTER = (
    "volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager"
)
LOCAL_RESIDENT_RUNTIME_ADAPTER = (
    "volundr.adapters.outbound.local_resident_runtime.LocalContainerResidentRuntimeController"
)
HOST_PROCESS_RESIDENT_RUNTIME_ADAPTER = (
    "volundr.adapters.outbound.host_resident_runtime.HostProcessResidentRuntimeController"
)
MINI_RESIDENTS_DIR = "~/.niuu/residents"
MINI_RESIDENT_CODEX_MODEL = "gpt-5.6-sol"
MINI_RESIDENT_CAPABILITIES = [
    "chat",
    "runtime.restart",
    "runtime.suspend",
    "logs",
    "metrics",
    "usage",
]
OPENCLAW_RESIDENT_SESSION_ADAPTER = (
    "volundr.adapters.outbound.openclaw_gateway.OpenClawResidentSessionController"
)
HERMES_RESIDENT_SESSION_ADAPTER = (
    "volundr.adapters.outbound.hermes_gateway.HermesResidentSessionController"
)
LOCAL_RAVN_IMAGE = (
    "ghcr.io/niuulabs/openshell@"
    "sha256:b17395a07b7dd19798c9e424a4139e2518a8b0e0bfee2305b5352a33140f383b"
)
LOCAL_NEMOCLAW_IMAGE = (
    "ghcr.io/nvidia/openshell-community/sandboxes/openclaw@"
    "sha256:b3d832b596ab6b7184a9dcb4ae93337ca32851a4f93b00765cc12de26baa3a9a"
)
LOCAL_NEMOHERMES_IMAGE = (
    "ghcr.io/nvidia/nemoclaw/hermes-sandbox-base@"
    "sha256:7e9378c50f291e6dd80b922e8b89e0e7edf21e4e3a80b8c2664be01976f59aa8"
)

CLUSTER_POD_MANAGER_DEFAULTS: dict[str, Any] = {
    "adapter": CLUSTER_POD_MANAGER_ADAPTER,
    "namespace": "volundr",
    "kubeconfig": "~/.kube/config",
    "skuld_image": "ghcr.io/niuulabs/skuld:0.2.0",
    "db_host": "host.k3d.internal",
    "ingress_class": "traefik",
}

MINI_POD_MANAGER_DEFAULTS: dict[str, Any] = {
    "adapter": MINI_POD_MANAGER_ADAPTER,
    "workspaces_dir": "~/.niuu/workspaces",
    "claude_binary": "claude",
}

OPENSHELL_POD_MANAGER_DEFAULTS: dict[str, Any] = {
    "adapter": OPENSHELL_POD_MANAGER_ADAPTER,
    "gateway_endpoint": "openshell.openshell.svc.cluster.local:8080",
    "gateway_public_url": "",
    "token_url": "https://keycloak.niuu.world/realms/volundr/protocol/openid-connect/token",
    "client_id": "openshell-volundr-agent",
    "sandbox_image": "ghcr.io/niuulabs/skuld:openshell-codex-openbao-20260709-7",
    "sandbox_command": ["/opt/niuu/bin/python", "-m", "skuld"],
    "service_port": 9200,
}


def _effective_bifrost_config(settings: CLISettings) -> BifrostConfig:
    """Return ``settings.bifrost``, forced into verified oidc mode when the host is.

    ``host_auth.mode: oidc`` is a platform-wide claim that every inbound
    identity path on this host is signature-verified (see
    ``cli.config.AuthConfig``); Bifröst does not read the shared
    ``AUTH_MODE``/``IDENTITY__ADAPTER`` env vars ``auth_adapter_env()``
    computes for the other co-hosted services (it is configured via
    ``BIFROST_CONFIG`` instead — see that function's docstring), so nothing
    else makes this claim true for it. This mirrors ``auth_adapter_env()``'s
    own behaviour: it unconditionally overrides the computed adapters for
    'oidc', not merging with whatever the operator separately configured, so
    a stale ``bifrost.auth_mode: open`` (or 'pat'/'mesh') in config.yaml can
    never leave a host claiming oidc coverage it doesn't have. 'none' leaves
    ``settings.bifrost`` untouched — Bifröst stays open (or whatever the
    operator configured) exactly as today, per the no-auth owner contract.

    Raises (rather than silently overriding) when the operator explicitly
    configured an ``bifrost.auth_mode`` this override would discard (checked
    via ``model_fields_set``, not a comparison against the default — the
    default IS 'open', so a comparison couldn't tell "operator wrote open"
    from "operator wrote nothing"). ``bifrost.pat_revocation`` is not
    discarded: it survives the ``model_copy`` below untouched and
    ``bifrost.app._build_pat_revocation_validator`` applies it under 'oidc'
    too, whenever the verified bearer happens to be a PAT — see
    ``bifrost.adapters.auth.oidc.OidcAuthAdapter``.

    Does not raise while the ``bifrost`` plugin itself is disabled — a
    stale ``bifrost.auth_mode`` in config.yaml is inert then (nothing reads
    ``BIFROST_CONFIG``), and demanding it be removed anyway would be a
    second, redundant way to say what
    ``CLISettings._OIDC_UNCOVERED_PLUGINS['bifrost']`` already says.
    """
    if settings.host_auth.mode != "oidc":
        return settings.bifrost

    if settings.plugins.enabled.get("bifrost", True) and (
        "auth_mode" in settings.bifrost.model_fields_set
    ):
        raise typer.BadParameter(
            f"bifrost.auth_mode: {settings.bifrost.auth_mode!r} is configured, but "
            "host_auth.mode: oidc always overrides it to 'oidc' (every inbound path "
            "on this host must be verified). Remove bifrost.auth_mode from "
            "config.yaml, or set host_auth.mode: none if Bifröst should keep its "
            "own separately-configured auth mode instead.",
            param_hint="bifrost.auth_mode",
        )

    from bifrost.auth import AuthMode as BifrostAuthModeEnum
    from cli.config import auth_adapter_env

    # Reuse the same computed kwargs every other co-hosted service's oidc
    # slot gets (IDENTITY__KWARGS / RAVN_API_AUTH__KWARGS / ... are all the
    # same JSON — see auth_adapter_env()) rather than recomputing them.
    oidc_kwargs = json.loads(auth_adapter_env(settings.host_auth)["IDENTITY__KWARGS"])
    return settings.bifrost.model_copy(
        update={
            "auth_mode": BifrostAuthModeEnum.OIDC,
            "oidc_kwargs": oidc_kwargs,
        }
    )


def _resolve_local_pod_manager_env(settings: CLISettings) -> dict[str, str]:
    """Build env overrides for Volundr host-local runtime configuration."""
    from pathlib import Path

    kwargs = dict(settings.pod_manager.adapter_kwargs())
    workspaces_dir = Path(str(kwargs.get("workspaces_dir", "~/.niuu/workspaces"))).expanduser()
    home_dir = workspaces_dir.parent / "home"

    env = {
        "POD_MANAGER__ADAPTER": settings.pod_manager.adapter,
        "STORAGE__KWARGS__WORKSPACE_MOUNT_PATH": str(workspaces_dir),
        "STORAGE__KWARGS__HOME_MOUNT_PATH": str(home_dir),
        "GIT__VALIDATE_ON_CREATE": "false",
        "RESIDENT_RUNTIMES": json.dumps(_mini_resident_runtimes_config(settings)),
        "BIFROST_CONFIG": _effective_bifrost_config(settings).model_dump_json(),
        "CREDENTIAL_STORE": json.dumps(
            {
                "adapter": ("volundr.adapters.outbound.file_credential_store.FileCredentialStore"),
                "kwargs": {"base_dir": str(workspaces_dir.parent / "credentials")},
            }
        ),
    }
    if settings.compute is not None:
        env.pop("POD_MANAGER__ADAPTER")
        env["POD_MANAGER"] = json.dumps(
            {
                "adapter": settings.pod_manager.adapter,
                "runtime_backend": settings.pod_manager.runtime_backend or "vm",
                "kwargs": kwargs,
            }
        )
    else:
        for key, value in kwargs.items():
            env[f"POD_MANAGER__KWARGS__{key.upper()}"] = str(value)
    seeds = model_server_seed_connections(settings)
    if seeds:
        env["INTEGRATIONS__SEED_CONNECTIONS"] = json.dumps(seeds)
    if settings.mode == "mini":
        from cli.config import auth_adapter_env

        env.update(auth_adapter_env(settings.host_auth))
    return env


MODEL_SERVER_SLUG = "model-server"
MODEL_SERVER_OWNER_ID = "dev-user"


def _session_platform_url(settings: CLISettings) -> str:
    """Where a session container reaches this platform (and so the gateway)."""
    kwargs = settings.pod_manager.adapter_kwargs()
    configured = str(kwargs.get("platform_url") or "").strip()
    if configured:
        return configured.rstrip("/")
    if "DockerContainerPodManager" in settings.pod_manager.adapter:
        return f"http://niuu:{settings.server.port}"
    return f"http://127.0.0.1:{settings.server.port}"


def model_server_seed_connections(settings: CLISettings) -> list[dict[str, Any]]:
    """One "Model server" AI-provider connection per self-hosted gateway provider.

    Read from the merged gateway providers, not the docker section: inside the
    platform container the bundle's settings arrive as ``NIUU_BIFROST`` only.
    A self-hosted provider without a base URL (the gateway's built-in ``local``
    entry) is not a server anyone can reach and is skipped.

    Sessions launched with the connection get the gateway URL and gateway
    token from its config (``env_from_config`` on the catalog entry), which is
    what points Claude Code and Codex at the served models and lets them
    authenticate to it. Seeded again on every start, so the model list follows
    the stack settings.

    The token is the named ``OPEN_GATEWAY_TOKEN`` sentinel, never a real
    credential: this function only ever runs where the bifrost plugin is
    active, and ``CLISettings._oidc_covers_every_enabled_mount`` already
    refuses to start with ``host_auth.mode: oidc`` and the bifrost plugin both
    enabled (no per-session credential exists to mint yet), so every call here
    is under ``none`` or ``envoy``, where the gateway trusts every caller and
    the sentinel is all a session needs.
    """
    from cli.services.compose_bundle import MODEL_SERVER_PROVIDERS, bifrost_providers
    from volundr.domain.model_gateway import OPEN_GATEWAY_TOKEN

    gateway_url = f"{_session_platform_url(settings)}/api/v1/bifrost"
    seeds: list[dict[str, Any]] = []
    for provider, config in bifrost_providers(settings).items():
        if provider not in MODEL_SERVER_PROVIDERS or not str(config.get("base_url") or "").strip():
            continue
        seeds.append(
            {
                "owner_type": "user",
                "owner_id": MODEL_SERVER_OWNER_ID,
                "integration_type": "ai_provider",
                "adapter": "",
                "credential_name": f"{MODEL_SERVER_SLUG}-{provider}",
                "slug": MODEL_SERVER_SLUG,
                "enabled": True,
                "credential": {"secret_type": "generic", "data": {"provider": provider}},
                "config": {
                    "provider": provider,
                    "gateway_url": gateway_url,
                    "token": OPEN_GATEWAY_TOKEN,
                    "models": list(config["models"]),
                },
            }
        )
    return seeds


def _mini_resident_runtimes_config(settings: CLISettings) -> dict[str, Any]:
    """Resident runtime configuration for the selected local resident runtime."""
    builders = {
        "process": _host_process_resident_runtimes,
        "docker": _docker_resident_runtimes,
    }
    return builders[settings.residents.runtime](settings)


def _host_resident_platform_url(settings: CLISettings) -> str:
    """Where a resident process on this host reaches the platform."""
    host = settings.server.host.strip()
    if host in {"", "0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings.server.port}"


def _ravn_local_profile(
    platform_url: str,
    *,
    description: str,
    runtime_values: dict[str, Any],
) -> dict[str, Any]:
    """The ``ravn-local`` profile, identical across the local resident runtimes."""
    return {
        "id": "ravn-local",
        "display_name": "Resident Ravn (Local)",
        "description": description,
        "backend": "local",
        "engine": "ravn",
        "capabilities": MINI_RESIDENT_CAPABILITIES,
        "default_model": MINI_RESIDENT_CODEX_MODEL,
        "allowed_models": [MINI_RESIDENT_CODEX_MODEL],
        "catalog_vendors": [],
        "labels": ["resident", "ravn", "local"],
        "deployment": {
            "values": {
                **runtime_values,
                "broker": {
                    "cliType": "codex-ws",
                    "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "skipPermissions": True,
                },
                "session": {"reasoningEffort": "high"},
                "resident": {
                    "persona": "product-steward",
                    "llm": {
                        "provider": {
                            "adapter": "ravn.adapters.llm.bifrost.BifrostAdapter",
                            "kwargs": {"base_url": f"{platform_url}/api/v1/bifrost"},
                        }
                    },
                    "platform": {"enabled": True, "baseUrl": platform_url},
                    "wakefulness": {"enabled": True},
                },
            }
        },
    }


def _host_process_resident_runtimes(settings: CLISettings) -> dict[str, Any]:
    """Ravn residents as host processes; no container engine involved."""
    platform_url = _host_resident_platform_url(settings)
    return {
        "controllers": [
            {
                "adapter": HOST_PROCESS_RESIDENT_RUNTIME_ADAPTER,
                "kwargs": {
                    "residents_dir": MINI_RESIDENTS_DIR,
                    "volundr_api_url": platform_url,
                },
            }
        ],
        "session_controllers": [],
        "profiles": [
            _ravn_local_profile(
                platform_url,
                description="Long-lived Ravn resident running as processes on this host",
                runtime_values={},
            )
        ],
    }


def _docker_resident_runtimes(settings: CLISettings) -> dict[str, Any]:
    """Resident images through the local Docker Engine (``residents.runtime: docker``)."""
    local_platform_url = f"http://host.docker.internal:{settings.server.port}"
    local_bifrost_url = f"{local_platform_url}/api/v1/bifrost"
    configured_models = [
        model for provider in settings.bifrost.providers.values() for model in provider.models
    ]
    model_ids = list(dict.fromkeys(configured_models)) or [MINI_RESIDENT_CODEX_MODEL]
    resident_model_ids = [f"niuu/{model}" for model in model_ids]
    resident_default_model = resident_model_ids[0]
    openclaw_models = [
        {
            "id": model,
            "name": model,
            "reasoning": True,
            "input": ["text"],
            "contextWindow": 131072,
            "maxTokens": 32768,
        }
        for model in model_ids
    ]
    common_capabilities = MINI_RESIDENT_CAPABILITIES
    session_capabilities = ["session.list", "session.create", "session.delete"]
    return {
        "controllers": [
            {
                "adapter": LOCAL_RESIDENT_RUNTIME_ADAPTER,
                "kwargs": {
                    "residents_dir": MINI_RESIDENTS_DIR,
                    "volundr_api_url": local_platform_url,
                },
            }
        ],
        "session_controllers": [
            {
                "adapter": OPENCLAW_RESIDENT_SESSION_ADAPTER,
                "runtime_backend": "local",
            },
            {
                "adapter": HERMES_RESIDENT_SESSION_ADAPTER,
                "runtime_backend": "local",
            },
        ],
        "profiles": [
            _ravn_local_profile(
                local_platform_url,
                description="Long-lived Ravn resident hosted by the local container engine",
                runtime_values={
                    "image": LOCAL_RAVN_IMAGE,
                    "runtime": {"service": {"name": "skuld", "port": 9200}},
                },
            ),
            {
                "id": "nemoclaw-local",
                "display_name": "NemoClaw (Local)",
                "description": "NVIDIA OpenClaw resident hosted by the local container engine",
                "backend": "local",
                "engine": "openclaw",
                "capabilities": [*common_capabilities, *session_capabilities, "steer", "interrupt"],
                "default_model": resident_default_model,
                "allowed_models": resident_model_ids,
                "catalog_vendors": [],
                "model_prefix": "niuu/",
                "labels": ["resident", "nemoclaw", "openclaw", "local"],
                "deployment": {
                    "values": {
                        "image": LOCAL_NEMOCLAW_IMAGE,
                        "runtime": {
                            "processMode": "replace",
                            "service": {"name": "openclaw", "port": 18789},
                            "processes": [
                                {
                                    "name": "openclaw",
                                    "command": [
                                        "openclaw",
                                        "gateway",
                                        "run",
                                        "--bind",
                                        "lan",
                                        "--auth",
                                        "token",
                                        "--port",
                                        "18789",
                                    ],
                                    "files": {
                                        "/sandbox/workspace/.openclaw/openclaw.json": json.dumps(
                                            {
                                                "gateway": {"bind": "lan", "mode": "local"},
                                                "agents": {
                                                    "defaults": {
                                                        "model": {
                                                            "primary": resident_default_model
                                                        },
                                                        "thinkingDefault": "high",
                                                        "workspace": "/sandbox/workspace",
                                                    }
                                                },
                                                "models": {
                                                    "mode": "merge",
                                                    "providers": {
                                                        "niuu": {
                                                            "baseUrl": f"{local_bifrost_url}/v1",
                                                            "apiKey": "local-mini",
                                                            "api": "openai-completions",
                                                            "models": openclaw_models,
                                                        }
                                                    },
                                                },
                                            },
                                            indent=2,
                                        )
                                    },
                                }
                            ],
                        },
                        "resident": {
                            "llm": {"provider": {"kwargs": {"base_url": local_bifrost_url}}},
                            "platform": {"enabled": True, "baseUrl": local_platform_url},
                        },
                    }
                },
            },
            {
                "id": "nemohermes-local",
                "display_name": "NemoHermes (Local)",
                "description": "NVIDIA NemoHermes resident hosted by the local container engine",
                "backend": "local",
                "engine": "hermes",
                "capabilities": [
                    *common_capabilities,
                    *session_capabilities,
                    "interrupt",
                    "approvals",
                ],
                "default_model": resident_default_model,
                "allowed_models": resident_model_ids,
                "catalog_vendors": [],
                "model_prefix": "niuu/",
                "labels": ["resident", "nemohermes", "hermes", "local"],
                "deployment": {
                    "values": {
                        "image": LOCAL_NEMOHERMES_IMAGE,
                        "env": {"HERMES_ALLOW_ROOT_GATEWAY": "1"},
                        "runtime": {
                            "processMode": "replace",
                            "service": {"name": "hermes", "port": 18789},
                            "processes": [
                                {
                                    "name": "hermes",
                                    "command": [
                                        "/opt/hermes/.venv/bin/python",
                                        "/opt/hermes/.venv/bin/hermes",
                                        "gateway",
                                        "run",
                                        "--replace",
                                        "--force",
                                        "--no-supervise",
                                        "--accept-hooks",
                                    ],
                                }
                            ],
                        },
                        "resident": {
                            "llm": {
                                "provider": {"kwargs": {"base_url": f"{local_bifrost_url}/v1"}}
                            },
                            "platform": {"enabled": True, "baseUrl": local_platform_url},
                        },
                    }
                },
            },
        ],
    }


def _prompt_mode_selection() -> str:
    """Prompt the user for mini, OpenShell, cluster, or docker mode."""
    typer.echo("Select operating mode:")
    typer.echo("  [1] mini   — local processes, no cluster needed (default)")
    typer.echo("  [2] openshell — OpenShell gateway sandboxes")
    typer.echo("  [3] cluster — session pods run in k3d/k3s cluster")
    typer.echo(
        "  [4] docker — whole stack as containers on this host (any Docker host, e.g. DGX Spark)"
    )
    choice = typer.prompt("Choice", default="1", show_default=False)
    if choice.strip() in ("2", "openshell"):
        return "openshell"
    if choice.strip() in ("3", "cluster"):
        return "cluster"
    if choice.strip() in ("4", "docker"):
        return "docker"
    return "mini"


def _build_init_config(mode: str) -> dict[str, Any]:
    """Build the initial config dict for the selected mode."""
    if mode == "docker":
        from cli.config import DockerConfig

        return {"mode": "docker", "docker": DockerConfig().model_dump(exclude={"vllm"})}
    if mode == "cluster":
        return {
            "mode": "cluster",
            "pod_manager": dict(CLUSTER_POD_MANAGER_DEFAULTS),
        }
    if mode == "openshell":
        return {
            "mode": "openshell",
            "pod_manager": dict(OPENSHELL_POD_MANAGER_DEFAULTS),
        }
    return {
        "mode": "mini",
        "pod_manager": dict(MINI_POD_MANAGER_DEFAULTS),
    }


def _route_inventory_payload(inventory: list[Any] | tuple[Any, ...]) -> list[dict[str, Any]]:
    """Convert route inventory records into JSON-friendly dicts."""
    return [
        {
            "name": item.name,
            "prefixes": list(item.prefixes),
            "source": item.source,
            "plugin": item.plugin_name,
        }
        for item in inventory
    ]


def create_platform_commands(
    registry: PluginRegistry,
    settings: CLISettings,
    manager: ServiceManager,
) -> typer.Typer:
    """Create the ``platform`` command group with dynamic service flags."""
    platform_app = typer.Typer(
        name="platform",
        help="Manage the platform (up, down, status, init). "
        "`niuu up|down|status` are shortcuts for these with default flags.",
        no_args_is_help=True,
    )

    # Collect service definitions from all plugins (enabled and disabled)
    # so future plugins automatically get a flag.
    service_defs = _collect_service_definitions(registry)

    # Register ``up`` with dynamic signature
    up_fn = _build_up_callback(service_defs, manager, settings)
    platform_app.command(name="up")(up_fn)

    @platform_app.command()
    def down() -> None:
        """Stop all running services."""
        if settings.mode == "docker":
            from cli.commands.stack import stack_down

            stack_down(settings)
            return
        asyncio.run(_shutdown(manager))
        typer.echo("Services stopped.")

    @platform_app.command()
    def status() -> None:
        """Show health of all registered services."""
        if settings.mode == "docker":
            from cli.commands.stack import stack_status

            stack_status(settings)
            return
        typer.echo(f"Mode: {settings.mode}")
        typer.echo(f"Pod manager: {settings.pod_manager.adapter.rsplit('.', 1)[-1]}")
        typer.echo()

        if settings.mode == "cluster":
            kwargs = settings.pod_manager.adapter_kwargs()
            typer.echo("Cluster info:")
            typer.echo(f"  Namespace: {kwargs.get('namespace', 'volundr')}")
            typer.echo(f"  Kubeconfig: {kwargs.get('kubeconfig', '~/.kube/config')}")
            typer.echo()
        elif settings.mode == "openshell":
            kwargs = settings.pod_manager.adapter_kwargs()
            typer.echo("OpenShell info:")
            endpoint = kwargs.get("gateway_endpoint", "openshell.openshell.svc.cluster.local:8080")
            typer.echo(f"  Gateway endpoint: {endpoint}")
            public_url = kwargs.get("gateway_public_url") or "(from OpenShell service exposure)"
            typer.echo(f"  Gateway public URL: {public_url}")
            client_id = kwargs.get("client_id", "openshell-volundr-agent")
            typer.echo(f"  OIDC client: {client_id}")
            image = kwargs.get(
                "sandbox_image",
                "ghcr.io/niuulabs/skuld:openshell-codex-openbao-20260709-7",
            )
            typer.echo(f"  Sandbox image: {image}")
            typer.echo()

        plugins = registry.plugins
        if not plugins:
            typer.echo("No services registered.")
            return
        typer.echo("Services:")
        for svc_name in sorted(service_defs.keys()):
            svc_status = manager.services.get(svc_name)
            state = svc_status.state.value if svc_status else "not started"
            svc_def = service_defs[svc_name]
            typer.echo(f"  {svc_name}: {state} — {svc_def.description}")

    @platform_app.command()
    def inventory(
        host_profile: str = typer.Option(
            "full",
            "--host-profile",
            help="Host profile used to resolve mounted route domains.",
        ),
        mounts: str = typer.Option(
            "",
            "--mounts",
            help="Comma-separated route domains to inventory instead of the profile default.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print route inventory as JSON.",
        ),
        out: str = typer.Option(
            "",
            "--out",
            help="Optional file path to write the JSON route inventory report.",
        ),
    ) -> None:
        """Show or export the route domains mounted by the niuu host."""
        from niuu.app import collect_route_inventory, parse_enabled_mounts

        try:
            enabled_mounts = parse_enabled_mounts(mounts)
            inventory = collect_route_inventory(
                registry=registry,
                host_profile=host_profile,
                enabled_mounts=enabled_mounts,
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

        payload = _route_inventory_payload(inventory)

        if out:
            output_path = Path(out)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(f"{json.dumps(payload, indent=2)}\n")
            typer.echo(f"Wrote route inventory to {output_path}")

        if json_output:
            typer.echo(json.dumps(payload, indent=2))
            return

        typer.echo(f"Host profile: {host_profile}")
        for item in payload:
            prefixes = ", ".join(item["prefixes"]) or "(none)"
            plugin = item["plugin"] or "internal"
            typer.echo(f"  {item['name']}: {prefixes} [{item['source']}/{plugin}]")

    @platform_app.command()
    def init() -> None:
        """Run the first-time setup wizard."""
        typer.echo("Running first-time setup...\n")

        config_path = Path(
            os.environ.get("NIUU_CONFIG", str(Path.home() / ".niuu" / "config.yaml"))
        ).expanduser()
        config_path.parent.mkdir(parents=True, exist_ok=True)

        if config_path.exists():
            overwrite = typer.confirm(
                f"Config already exists at {config_path}. Overwrite?",
                default=False,
            )
            if not overwrite:
                typer.echo("Aborted — existing config preserved.")
                raise typer.Exit(0)

        mode = _prompt_mode_selection()
        config_data = _build_init_config(mode)

        import yaml

        config_path.write_text(yaml.safe_dump(config_data, default_flow_style=False))
        typer.echo(f"\n  Config written to {config_path}")
        typer.echo(f"\nSetup complete ({mode} mode). Run 'niuu platform up' to start.")

    @platform_app.command(hidden=True)
    def skuld() -> None:
        """Run a Skuld broker instance (internal, one per session)."""
        from skuld.broker import main as skuld_main

        skuld_main()

    @platform_app.command(
        hidden=True,
        context_settings={
            "allow_extra_args": True,
            "ignore_unknown_options": True,
            "help_option_names": [],
        },
    )
    def ravn(ctx: typer.Context) -> None:
        """Run the Ravn CLI (internal, host-process residents in a compiled binary)."""
        from ravn.cli.commands import app as ravn_app

        ravn_app(args=list(ctx.args), prog_name="ravn")

    return platform_app
