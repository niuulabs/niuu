"""Configuration settings for Völundr.

Configuration is loaded from YAML, with environment variables overriding.

Config file locations (first found wins):
- ./config.yaml
- /etc/volundr/config.yaml

Environment variable override format:
- Use double underscore for nested fields: DATABASE__HOST, GIT__VALIDATE_ON_CREATE
- Or use the specific prefixes for backward compatibility: DATABASE_HOST, GITHUB_TOKEN

All configuration MUST flow through the Settings class.
"""

import os
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from bifrost.config import BifrostConfig
from niuu.config import (
    CorsConfig,
    DynamicAdapterConfig,
    GitHubConfig,
    GitHubInstance,
    GitLabConfig,
    GitLabInstance,
    HttpAuthAdapterConfig,
    InstanceRegistryConfig,
)
from niuu.config_models import (
    DatabaseConfig,
    SessionDefinitionConfig,
    WorkloadIdentityConfig,
    default_session_definitions,
)
from ravn.config import PersonaSourceConfig
from volundr.domain.models import (
    IntegrationType,
    ResidentBackend,
    ResidentCapability,
    ResidentEngine,
    SecretType,
)

__all__ = ["GitHubInstance", "GitLabInstance"]


# Config file search paths (in order of priority).
# NIUU_CONFIG env var (set by the CLI --config flag) takes precedence.
def _config_paths() -> list[Path]:
    env = os.environ.get("NIUU_CONFIG")
    if env:
        return [Path(env)]
    return [
        Path("./config.yaml"),
        Path("/etc/volundr/config.yaml"),
    ]


class LocalGitConfig(BaseModel):
    """Configuration for local git workspace operations."""

    subprocess_timeout: float = Field(
        default=30.0,
        description="Maximum time in seconds a git/gh subprocess may run before being killed.",
    )


class LocalMountsConfig(BaseModel):
    """Configuration for local filesystem mount support."""

    enabled: bool = Field(
        default=False,
        description="Enable local path mounts as session workspace sources.",
    )
    mini_mode: bool = Field(
        default=False,
        description="Running in mini/local mode (CLI). Enables local-only UI features.",
    )
    allow_root_mount: bool = Field(
        default=False,
        description="Allow mounting the root filesystem (/). Requires enabled=true.",
    )
    allowed_prefixes: list[str] = Field(
        default_factory=list,
        description=(
            "Restrict mountable host paths to these prefixes. Empty = allow all when enabled."
        ),
    )
    default_read_only: bool = Field(
        default=True,
        description="Default read_only flag for new mount mappings.",
    )


class ExternalSessionProviderConfig(BaseModel):
    """Configuration for a single external session provider.

    The ``adapter`` key is a fully-qualified class path. All other
    fields are forwarded as **kwargs to the adapter constructor.

    Example YAML::

        external_sessions:
          enabled: true
          providers:
            - adapter: "volundr.adapters.outbound.external_sessions.ClaudeCodeSessionProvider"
              projects_dir: "~/.claude/projects"
            - adapter: "volundr.adapters.outbound.external_sessions.CodexSessionProvider"
              sessions_dir: "~/.codex/sessions"
    """

    adapter: str
    kwargs: dict[str, Any] = Field(default_factory=dict)


def _default_external_session_providers() -> list[ExternalSessionProviderConfig]:
    """Built-in providers: Claude Code and Codex local stores."""
    return [
        ExternalSessionProviderConfig(
            adapter="volundr.adapters.outbound.external_sessions.ClaudeCodeSessionProvider",
        ),
        ExternalSessionProviderConfig(
            adapter="volundr.adapters.outbound.external_sessions.CodexSessionProvider",
        ),
    ]


class ExternalSessionsConfig(BaseModel):
    """Configuration for discovering and importing external CLI sessions.

    When ``enabled`` is left unset, discovery follows ``local_mounts.mini_mode``
    — host session stores are only reachable when Volundr runs on the host.
    """

    enabled: bool | None = Field(
        default=None,
        description=(
            "Enable external session discovery. None (default) follows local_mounts.mini_mode."
        ),
    )
    providers: list[ExternalSessionProviderConfig] = Field(
        default_factory=_default_external_session_providers,
        description="External session provider adapters (dynamic adapter pattern).",
    )


class ProvisioningConfig(BaseModel):
    """Configuration for the session provisioning readiness polling."""

    timeout_seconds: float = Field(
        default=300.0,
        description="Maximum time to wait for infrastructure readiness in seconds.",
    )
    initial_delay_seconds: float = Field(
        default=5.0,
        description="Initial delay before starting readiness polls in seconds.",
    )


def _default_auto_approval_allowlist() -> list[str]:
    return [
        r"^\s*\./start-dev(?:\s|$)",
        r"^\s*\./stop-dev(?:\s|$)",
        r"^\s*(?:pwd|ls|find|rg|grep|cat|sed|awk|head|tail|wc)(?:\s|$)",
        r"^\s*git\s+(?:status|diff|log|show|branch|rev-parse)(?:\s|$)",
        (
            r"^\s*(?:pnpm\s+(?:exec\s+vitest|--filter\s+[^\s]+\s+"
            r"(?:test|typecheck|build))|npm\s+(?:test|run\s+test)|"
            r"\.venv/bin/pytest|pytest|python3?\s+-m\s+pytest|uv\s+run\s+pytest)(?:\s|$)"
        ),
    ]


def _default_auto_approval_denylist() -> list[str]:
    return [
        (
            r"(?:^|[;&|]\s*)(?:sudo|su|rm\s+-rf|mkfs|dd\b|shred|wipefs|fdisk|"
            r"parted|diskutil|kill(?:all)?\b|pkill\b|launchctl|systemctl|"
            r"chmod\s+-R|chown\s+-R)\b"
        ),
        (
            r"\b(?:git\s+(?:reset\s+--hard|clean\s+-f|checkout\s+--)|"
            r"pnpm\s+(?:install|add|remove)|npm\s+(?:install|i|add|uninstall)|"
            r"brew\s+(?:install|uninstall))\b"
        ),
        r"\b(?:curl|wget)\b[^|]*\|\s*(?:sh|bash)\b",
        r"\bfind\b.*(?:\s-delete\b|-exec\s+(?:rm|sh|bash)\b)",
        r"(?:^|\s)>\s*/(?:dev|etc|bin|sbin|usr|System)\b",
        r"(?:^|\s)--force(?:\s|$)",
    ]


class PermissionAutoApprovalConfig(BaseModel):
    """Server-side policy for browser-displayed permission auto approvals."""

    enabled: bool = Field(
        default=True,
        description="Allow the UI to auto-approve permission requests that match this policy.",
    )
    delay_seconds: int = Field(
        default=5,
        ge=2,
        le=30,
        description="Countdown duration before an allowlisted request is auto-approved.",
    )
    allowlist: list[str] = Field(
        default_factory=_default_auto_approval_allowlist,
        description="Regex patterns that are eligible for auto approval.",
    )
    denylist: list[str] = Field(
        default_factory=_default_auto_approval_denylist,
        description="Regex patterns that are never eligible for auto approval.",
    )


class LoggingConfig(BaseSettings):
    """Logging configuration.

    Supports legacy LOG_LEVEL and LOG_FORMAT aliases.
    """

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    level: str = Field(default="info", validation_alias=AliasChoices("level", "LOG_LEVEL"))
    format: str = Field(default="text", validation_alias=AliasChoices("format", "LOG_FORMAT"))


class PodManagerConfig(BaseModel):
    """Dynamic pod manager adapter configuration.

    The ``adapter`` field is a fully-qualified class path. All other
    fields are forwarded as **kwargs to the adapter constructor.

    Example YAML::

        pod_manager:
          adapter: "volundr.adapters.outbound.flux.FluxPodManager"
          namespace: "volundr"
          chart_name: "skuld"
          ...
    """

    adapter: str = Field(
        default="volundr.adapters.outbound.flux.FluxPodManager",
        description="Fully-qualified class path for the PodManager adapter.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra kwargs forwarded to the adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )


def _default_codex_credential_broker() -> DynamicAdapterConfig:
    return DynamicAdapterConfig(
        adapter=("volundr.adapters.outbound.codex_credential_broker.DisabledCodexCredentialBroker")
    )


def _default_credential_enrollment_runner() -> DynamicAdapterConfig:
    return DynamicAdapterConfig(
        adapter=(
            "volundr.adapters.outbound.credential_enrollment_runner."
            "UnsupportedCredentialEnrollmentRunner"
        )
    )


class ResidentProfileConfig(BaseModel):
    """One operator-approved resident backend and engine combination."""

    id: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    display_name: str = Field(min_length=1, max_length=255)
    description: str = ""
    backend: ResidentBackend
    engine: ResidentEngine
    capabilities: list[ResidentCapability] = Field(default_factory=list)
    default_model: str = ""
    allowed_models: list[str] = Field(default_factory=list)
    catalog_vendors: list[str] = Field(
        default_factory=list,
        description=(
            "Bifrost model vendors accepted by this resident engine. Empty means all vendors."
        ),
    )
    model_prefix: str = Field(
        default="",
        description="Prefix added to canonical Bifrost model IDs for the resident engine.",
    )
    labels: list[str] = Field(default_factory=list)
    deployment: dict[str, Any] = Field(
        default_factory=dict,
        description="Backend-owned deployment input, never exposed through the profile API.",
    )


class ResidentSessionControllerConfig(BaseModel):
    """One dynamically configured resident engine protocol adapter."""

    adapter: str = Field(min_length=1)
    runtime_backend: ResidentBackend
    optional: bool = False
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class ResidentRuntimesConfig(BaseModel):
    """Configured resident deployment profiles for this Volundr target."""

    controllers: list[PodManagerConfig] = Field(
        default_factory=list,
        description=(
            "Additional dynamically configured resident runtime controllers. "
            "A resident-capable pod manager is registered automatically."
        ),
    )
    session_controllers: list[ResidentSessionControllerConfig] = Field(
        default_factory=lambda: [
            ResidentSessionControllerConfig(
                adapter=(
                    "volundr.adapters.outbound.openclaw_gateway.OpenClawResidentSessionController"
                ),
                runtime_backend=ResidentBackend.OPENSHELL,
                optional=True,
            )
        ],
        description="Dynamically configured resident engine protocol adapters.",
    )
    profiles: list[ResidentProfileConfig] = Field(default_factory=list)
    reconciliation_interval_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Interval between resident backend reconciliation passes.",
    )

    @model_validator(mode="after")
    def validate_unique_profile_ids(self) -> "ResidentRuntimesConfig":
        ids = [profile.id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("resident_runtimes.profiles ids must be unique")
        return self


class MCPServerEntry(BaseModel):
    """Configuration for an available MCP server."""

    name: str
    type: str = "stdio"
    command: str | None = None
    url: str | None = None
    args: list[str] = Field(default_factory=list)
    description: str = ""


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` without mutating either input."""
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(base_value, value)
        else:
            merged[key] = value
    return merged


def _merge_session_definition_configs(
    base: SessionDefinitionConfig,
    override: SessionDefinitionConfig,
) -> SessionDefinitionConfig:
    """Merge explicit override fields onto a built-in session definition."""
    merged = base.model_copy(deep=True)
    explicit_fields = set(getattr(override, "model_fields_set", set()))
    for field_name in explicit_fields:
        value = getattr(override, field_name)
        if field_name == "defaults":
            merged.defaults = _deep_merge_dicts(merged.defaults, value)
        else:
            setattr(merged, field_name, value)
    return merged


def merge_session_definitions(
    overrides: dict[str, SessionDefinitionConfig] | None,
) -> dict[str, SessionDefinitionConfig]:
    """Deep-merge configured session definition overrides onto built-in defaults."""
    merged = {
        key: definition.model_copy(deep=True)
        for key, definition in default_session_definitions().items()
    }
    for key, override in (overrides or {}).items():
        if key in merged:
            merged[key] = _merge_session_definition_configs(merged[key], override)
        else:
            merged[key] = override.model_copy(deep=True)
    return merged


class LaunchSpecConfig(BaseModel):
    """Configuration for a single system-scope launch spec.

    The unified blueprint replacing ProfileConfig + TemplateConfig.
    """

    name: str
    description: str = ""
    is_default: bool = False
    session_definition: str | None = None
    workload_type: str = "session"
    # Runtime config
    model: str | None = None
    system_prompt: str | None = None
    resource_config: dict[str, Any] = Field(default_factory=dict)
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    env_vars: dict[str, str] = Field(default_factory=dict)
    env_secret_refs: list[str] = Field(default_factory=list)
    workload_config: dict[str, Any] = Field(default_factory=dict)
    # Workspace
    repos: list[dict[str, Any]] = Field(default_factory=list)
    setup_scripts: list[str] = Field(default_factory=list)
    workspace_layout: dict[str, Any] = Field(default_factory=dict)
    cli_tool: str = ""


def _default_launch_specs() -> list[LaunchSpecConfig]:
    """Built-in launch catalog used when no config preloads specs."""
    return [
        LaunchSpecConfig(
            name="standard-claude",
            description="Default Claude Code session with modest resources.",
            is_default=True,
            session_definition="skuldClaude",
            workload_type="session",
            model="claude-sonnet-4-6",
            resource_config={"cpu": "1", "memory": "2Gi"},
            cli_tool="claude",
        ),
        LaunchSpecConfig(
            name="standard-codex",
            description="Default Codex session for OpenAI-backed coding work.",
            session_definition="skuldCodex",
            workload_type="session",
            model="gpt-5.4",
            resource_config={"cpu": "1", "memory": "2Gi"},
            cli_tool="codex",
        ),
    ]


class ChronicleConfig(BaseModel):
    """Chronicle feature configuration."""

    auto_create_on_stop: bool = Field(default=True)
    summary_model: str = Field(default="claude-opus-4-8")
    summary_max_tokens: int = Field(default=2000)
    retention_days: int | None = Field(default=None)  # None = keep forever


class ArchiveStoreConfig(BaseModel):
    """Dynamic archive store adapter configuration."""

    adapter: str = Field(
        default="volundr.adapters.outbound.archive_store.FileSystemArchiveStore",
        description="Fully-qualified class path for the archive store adapter.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra kwargs forwarded to the archive store adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )


class GitWorkflowConfig(BaseModel):
    """Git workflow configuration for PR-based development."""

    auto_branch: bool = Field(default=True)
    branch_prefix: str = Field(default="volundr/session")
    protect_main: bool = Field(default=True)
    default_merge_method: str = Field(default="squash")
    auto_merge_threshold: float = Field(default=0.9)
    notify_merge_threshold: float = Field(default=0.6)


class RabbitMQConfig(BaseModel):
    """RabbitMQ event sink configuration."""

    enabled: bool = Field(default=False)
    url: str = Field(default="amqp://guest:guest@localhost:5672/")
    exchange_name: str = Field(default="volundr.events")
    exchange_type: str = Field(default="topic")


class OtelConfig(BaseModel):
    """OpenTelemetry event sink configuration.

    Follows OTel GenAI semantic conventions (v1.39+).
    The exporter endpoint should point at an OTLP-compatible collector
    (Tempo, Jaeger, Grafana Alloy, etc.).
    """

    enabled: bool = Field(default=False)
    endpoint: str = Field(default="http://localhost:4317")
    protocol: str = Field(default="grpc")
    service_name: str = Field(default="volundr")
    provider_name: str = Field(default="anthropic")
    insecure: bool = Field(default=True)


class EventPipelineConfig(BaseModel):
    """Event pipeline configuration."""

    postgres_buffer_size: int = Field(default=1, ge=1)
    rabbitmq: RabbitMQConfig = Field(default_factory=RabbitMQConfig)
    otel: OtelConfig = Field(default_factory=OtelConfig)


class SessionLivenessConfig(BaseModel):
    """Liveness reconciliation for running sessions (INV-9).

    A session whose broker has died can otherwise sit in ``running`` forever
    with a stale ``chat_endpoint`` (clients then open a socket to a tombstone and
    see nothing). Two complementary mechanisms keep the row truthful:

    1. **Pod-status reconcile** (``reconcile_enabled``, ON by default). A periodic
       loop probes ``pod_manager.status()`` for active sessions and corrects the
       row when the runtime state diverges. Kubernetes sessions additionally
       recover failed rows from Ready HelmReleases and remove runtime resources
       behind terminal rows. Because it consults the authoritative pod manager
       rather than a heartbeat clock, it never false-reaps a quiet-but-alive
       session, so it is safe to enable by default.

    2. **Heartbeat reaper** (``enabled``, OFF by default). The legacy reaper marks
       running sessions that have gone silent — no activity heartbeat for
       ``stale_after_seconds`` — as ``stopped``. Brokers currently report activity
       only on STATE CHANGES, so a quiet-but-alive session can go silent for long
       stretches and would be falsely reaped; this mechanism stays secondary and
       off by default. Enable with a generous ``stale_after_seconds`` once
       periodic broker heartbeats land.
    """

    enabled: bool = Field(default=False)
    stale_after_seconds: int = Field(default=600, ge=30)
    check_interval_seconds: int = Field(default=120, ge=10)
    exempt_workload_types: list[str] = Field(
        default_factory=list,
        description=(
            "Workload types the heartbeat reaper never reaps. Use for "
            "long-lived workloads that idle by design — a quiet session is "
            "not a dead one (pod-status reconcile still catches real death)."
        ),
    )
    reconcile_enabled: bool = Field(
        default=True,
        description=(
            "Periodically reconcile session rows against pod_manager.status(). "
            "Pod-status authoritative, so it never false-reaps idle-but-alive sessions."
        ),
    )
    reconcile_interval_seconds: int = Field(
        default=60,
        ge=5,
        description="Interval between pod-status reconcile sweeps.",
    )


class ReplayConfig(BaseModel):
    """Replay-as-live WebSocket: re-emit recorded ``session_event_log`` frames,
    paced by the recorded ``ts`` deltas, so a live-session client renders a
    finished session (or a checked-in fixture) as if it were streaming live.

    The DB route is read-only and auth-gated (mirrors the already-served REST
    ``GET .../log`` replay), so it defaults ON. The fixture route serves
    synthetic data UNAUTHENTICATED and defaults OFF (enable only in dev/CI).
    """

    enabled: bool = Field(default=True)
    fixtures_enabled: bool = Field(default=False)
    default_speed: float = Field(default=1.0, gt=0)
    max_gap_seconds: float = Field(default=2.0, ge=0)
    # Unified read-path visibility default (SRD FR-7 / INV-10): internal
    # tool_use/tool_result blocks are HIDDEN by default across ALL three read
    # paths — live broadcast (``WebSocketChannel(show_internal=False)``), replay,
    # and cold-read (``GET .../log``). One default, one toggle wire-message
    # (``set_internal_visibility``), one ``filter_internal_blocks`` predicate, so
    # the dropped set is identical everywhere. This was historically ``True`` for
    # replay only, which diverged from the live default; it is now aligned to the
    # live default. Flip to ``True`` only if a deployment wants internals shown by
    # default on EVERY path (the toggle still works regardless).
    default_show_internal: bool = Field(default=False)
    page_size: int = Field(default=500, ge=1, le=5000)
    fixtures_dir: str | None = Field(default=None)

    def fixtures_dir_path(self) -> Path:
        """Resolve the fixtures directory (defaults to the packaged dir)."""
        if self.fixtures_dir:
            return Path(self.fixtures_dir)
        from volundr.replay.fixtures import default_fixtures_dir

        return default_fixtures_dir()


class SleipnirConfig(BaseModel):
    """Sleipnir platform event bus integration (optional).

    When ``enabled`` is True, Volundr creates a Sleipnir adapter and
    registers a :class:`~volundr.adapters.outbound.sleipnir_event_sink.SleipnirEventSink`
    in the event pipeline and forwards SSE broadcaster events to the platform bus.

    Example YAML::

        sleipnir:
          enabled: true
          adapter: "sleipnir.adapters.nats_transport.NatsTransport"
          kwargs:
            servers: ["nats://nats:4222"]
    """

    enabled: bool = Field(
        default=False,
        description="Enable Sleipnir platform event bus integration.",
    )
    adapter: str = Field(
        default="sleipnir.adapters.in_process.InProcessBus",
        description="Fully-qualified class path for the Sleipnir adapter.",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class PushNotificationConfig(BaseModel):
    """Push / attention notification fan-out (optional).

    When enabled, a session entering ``awaiting_input`` dispatches a push to the
    owner's registered devices through the configured NotificationChannel
    adapter. Off by default; the default adapter only logs.

    Example YAML::

        push:
          enabled: true
          adapter: "volundr.adapters.outbound.push_channels.ApnsNotificationChannel"
          min_urgency: 0.8
          kwargs:
            team_id: "ABCDE12345"
            key_id: "KEY1234567"
            bundle_id: "com.niuu.forge"
          secret_kwargs_env:
            private_key: "APNS_PRIVATE_KEY"
    """

    enabled: bool = Field(
        default=False,
        description="Enable push notifications for sessions that need attention.",
    )
    adapter: str = Field(
        default="volundr.adapters.outbound.push_channels.LoggingNotificationChannel",
        description="Fully-qualified NotificationChannel class path.",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )
    min_urgency: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Drop pushes below this urgency.",
    )


class IdentityConfig(BaseModel):
    """Dynamic identity adapter configuration.

    The ``adapter`` key is a fully-qualified class path.  All other
    fields in ``kwargs`` are forwarded to the constructor alongside
    the ``user_repository`` that main.py injects at runtime.

    Example YAML::

        identity:
          adapter: "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"
          kwargs:
            user_id_header: "x-auth-user-id"
            email_header: "x-auth-email"
    """

    adapter: str = Field(
        default="volundr.adapters.outbound.identity.AllowAllIdentityAdapter",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )
    role_mapping: dict[str, str] = Field(
        default_factory=lambda: {
            "admin": "volundr:admin",
            "developer": "volundr:developer",
            "viewer": "volundr:viewer",
        }
    )


class AuthorizationConfig(BaseModel):
    """Dynamic authorization adapter configuration.

    Example YAML::

        authorization:
          adapter: "volundr.adapters.outbound.authorization.SimpleRoleAuthorizationAdapter"
          kwargs: {}
    """

    adapter: str = Field(
        default="volundr.adapters.outbound.authorization.AllowAllAuthorizationAdapter",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )


class CredentialStoreConfig(BaseModel):
    """Dynamic credential store adapter configuration.

    The ``adapter`` key is a fully-qualified class path.  All other
    fields in ``kwargs`` are forwarded to the constructor.

    Example YAML::

        credential_store:
          adapter: "niuu.adapters.openbao_credential_store.OpenBaoCredentialStore"
          kwargs:
            url: "http://openbao:8200"
            mount_path: "volundr"
            auth_method: "token"
    """

    adapter: str = Field(
        default="volundr.adapters.outbound.memory_credential_store.MemoryCredentialStore",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )


class GatewayConfig(BaseModel):
    """Dynamic gateway adapter configuration.

    The ``adapter`` key is a fully-qualified class path. All other
    fields in ``kwargs`` are forwarded to the constructor.

    The gateway adapter provides configuration (gateway name, namespace,
    JWT settings) that is passed through to the Skuld Helm chart so each
    session can create its own HTTPRoute and SecurityPolicy resources.

    Example YAML::

        gateway:
          adapter: "volundr.adapters.outbound.k8s_gateway.K8sGatewayAdapter"
          kwargs:
            namespace: "volundr-sessions"
            gateway_name: "volundr-gateway"
            gateway_namespace: "volundr-system"
            gateway_domain: "sessions.example.com"
            issuer_url: "https://idp.example.com"
            audience: "volundr"
            jwks_uri: "https://idp.example.com/.well-known/jwks"
    """

    adapter: str = Field(
        default="volundr.adapters.outbound.k8s_gateway.InMemoryGatewayAdapter",
        description="Fully-qualified class path for the GatewayPort adapter.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra kwargs forwarded to the adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )


class SecretInjectionConfig(BaseModel):
    """Dynamic secret injection adapter configuration.

    The ``adapter`` key is a fully-qualified class path.  All other
    fields in ``kwargs`` are forwarded to the constructor.

    Example YAML::

        secret_injection:
          adapter: >-
            volundr.adapters.outbound.infisical_secret_injection
            .InfisicalAgentInjectionAdapter
          kwargs:
            infisical_url: "https://infisical.example.com"
            client_id: "..."
            client_secret: "..."
            namespace: "volundr-sessions"
    """

    adapter: str = Field(
        default="volundr.adapters.outbound.memory_secret_injection.InMemorySecretInjectionAdapter",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )


class ResourceProviderConfig(BaseModel):
    """Dynamic resource provider adapter configuration.

    The ``adapter`` key is a fully-qualified class path.  All other
    fields in ``kwargs`` are forwarded to the constructor.

    Example YAML::

        resource_provider:
          adapter: "volundr.adapters.outbound.k8s_resource_provider.K8sResourceProvider"
          kwargs:
            namespace: "volundr-sessions"
    """

    adapter: str = Field(
        default="volundr.adapters.outbound.static_resource_provider.StaticResourceProvider",
        description="Fully-qualified class path for the ResourceProvider adapter.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra kwargs forwarded to the adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )


class StorageConfig(BaseModel):
    """Dynamic storage adapter configuration.

    The ``adapter`` key is a fully-qualified class path.  All other
    fields in ``kwargs`` are forwarded to the constructor.

    Example YAML::

        storage:
          adapter: "volundr.adapters.outbound.k8s_storage_adapter.K8sStorageAdapter"
          kwargs:
            namespace: "volundr-sessions"
            home_storage_class: "volundr-home"
    """

    adapter: str = Field(
        default="volundr.adapters.outbound.k8s_storage.InMemoryStorageAdapter",
        description="Fully-qualified class path for the StoragePort adapter.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra kwargs forwarded to the adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )


class SessionContributorConfig(BaseModel):
    """Configuration for a single session contributor.

    The ``adapter`` key is a fully-qualified class path.  All other
    fields are forwarded as **kwargs to the constructor alongside
    injected port instances.

    Example YAML::

        session_contributors:
          - adapter: "volundr.adapters.outbound.contributors.CoreSessionContributor"
            base_domain: "volundr.local"
          - adapter: "volundr.adapters.outbound.contributors.LaunchSpecContributor"
    """

    adapter: str
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )


class OAuthSpecConfig(BaseModel):
    """OAuth2 provider specification in config."""

    authorize_url: str
    token_url: str
    revoke_url: str = ""
    scopes: list[str] = Field(default_factory=list)
    token_field_mapping: dict[str, str] = Field(default_factory=dict)
    extra_authorize_params: dict[str, str] = Field(default_factory=dict)
    extra_token_params: dict[str, str] = Field(default_factory=dict)


class OAuthClientConfig(BaseModel):
    """Client credentials for a single OAuth integration."""

    client_id: str
    client_secret: str


class OAuthConfig(BaseModel):
    """Top-level OAuth configuration."""

    redirect_base_url: str = ""
    clients: dict[str, OAuthClientConfig] = Field(default_factory=dict)


class IntegrationDefinitionConfig(BaseModel):
    """A single integration definition in the catalog."""

    slug: str
    name: str
    description: str = ""
    integration_type: str
    adapter: str = ""  # fully-qualified class path (empty for env-only integrations)
    icon: str = ""
    credential_schema: dict[str, Any] = Field(default_factory=dict)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    mcp_server: dict[str, Any] | None = None
    env_from_credentials: dict[str, str] = Field(default_factory=dict)
    auth_type: str = "api_key"
    oauth: OAuthSpecConfig | None = None
    file_mounts: dict[str, str] = Field(default_factory=dict)
    credential_enrollment: dict[str, str] | None = None


def _default_integration_definitions() -> list[IntegrationDefinitionConfig]:
    """Return the built-in integration catalog entries."""
    return [
        IntegrationDefinitionConfig(
            slug="github",
            name="GitHub",
            description="GitHub source control — repo browsing, clone, PRs, and MCP server",
            integration_type="source_control",
            adapter="volundr.adapters.outbound.github.GitHubProvider",
            icon="github",
            credential_schema={
                "required": ["token"],
                "properties": {
                    "token": {"label": "Personal Access Token", "type": "password"},
                },
            },
            config_schema={
                "properties": {
                    "name": {"label": "Display Name", "type": "string"},
                    "base_url": {
                        "label": "API URL",
                        "type": "url",
                        "default": "https://api.github.com",
                    },
                    "orgs": {"label": "Organizations", "type": "string[]"},
                },
            },
            mcp_server={
                "name": "github",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env_from_credentials": {"GITHUB_PERSONAL_ACCESS_TOKEN": "token"},
            },
        ),
        IntegrationDefinitionConfig(
            slug="gitlab",
            name="GitLab",
            description="GitLab source control — repo browsing, clone, MRs, and MCP server",
            integration_type="source_control",
            adapter="volundr.adapters.outbound.gitlab.GitLabProvider",
            icon="gitlab",
            credential_schema={
                "required": ["token"],
                "properties": {
                    "token": {"label": "Personal Access Token", "type": "password"},
                },
            },
            config_schema={
                "properties": {
                    "name": {"label": "Display Name", "type": "string"},
                    "base_url": {
                        "label": "Instance URL",
                        "type": "url",
                        "default": "https://gitlab.com",
                    },
                    "groups": {"label": "Groups", "type": "string[]"},
                },
            },
            mcp_server={
                "name": "gitlab",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-gitlab"],
                "env_from_credentials": {"GITLAB_PERSONAL_ACCESS_TOKEN": "token"},
            },
        ),
        IntegrationDefinitionConfig(
            slug="linear",
            name="Linear",
            description="Linear issue tracker — issue browsing, status updates, and MCP server",
            integration_type="issue_tracker",
            adapter="volundr.adapters.outbound.linear.LinearAdapter",
            icon="linear",
            credential_schema={
                "required": ["api_key"],
                "properties": {"api_key": {"label": "API Key", "type": "password"}},
            },
            mcp_server={
                "name": "linear",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-linear"],
                "env_from_credentials": {"LINEAR_API_KEY": "api_key"},
            },
            auth_type="api_key",
        ),
        IntegrationDefinitionConfig(
            slug="anthropic",
            name="Anthropic (Claude API)",
            description="Anthropic API key for Claude models",
            integration_type="ai_provider",
            icon="anthropic",
            credential_schema={
                "required": ["api_key"],
                "properties": {"api_key": {"label": "API Key", "type": "password"}},
            },
            env_from_credentials={"ANTHROPIC_API_KEY": "api_key"},
        ),
        IntegrationDefinitionConfig(
            slug="openai",
            name="OpenAI",
            description="OpenAI API key for GPT/Codex models",
            integration_type="ai_provider",
            icon="openai",
            credential_schema={
                "required": ["api_key"],
                "properties": {"api_key": {"label": "API Key", "type": "password"}},
            },
            env_from_credentials={"OPENAI_API_KEY": "api_key"},
        ),
        IntegrationDefinitionConfig(
            slug="codex",
            name="OpenAI Codex (ChatGPT)",
            description="User-scoped ChatGPT subscription login for Codex runtimes",
            integration_type="ai_provider",
            icon="openai",
            credential_schema={},
            auth_type="device_code",
            credential_enrollment={
                "method": "codex_device",
                "credential_field": "auth.json",
                "default_credential_name": "codex-credentials",
            },
        ),
        IntegrationDefinitionConfig(
            slug="telegram",
            name="Telegram",
            description="Telegram bot — notifications, session alerts, and dispatch commands",
            integration_type="messaging",
            icon="telegram",
            credential_schema={
                "required": ["bot_token", "chat_id"],
                "properties": {
                    "bot_token": {
                        "label": "Bot Token",
                        "type": "password",
                        "description": "Telegram bot API token (from @BotFather)",
                    },
                    "chat_id": {
                        "label": "Chat ID",
                        "type": "string",
                        "description": "Chat or channel ID to send notifications to",
                    },
                },
            },
            auth_type="api_key",
        ),
    ]


class IntegrationsConfig(BaseModel):
    """Integration catalog configuration."""

    definitions: list[IntegrationDefinitionConfig] = Field(
        default_factory=_default_integration_definitions,
    )
    seed_connections: list["SeededIntegrationConnectionConfig"] = Field(
        default_factory=list,
        description=(
            "Integration connections to seed into the credential store and "
            "integration repository at startup."
        ),
    )


class SeededIntegrationCredentialConfig(BaseModel):
    """Credential payload to seed for an integration connection."""

    secret_type: SecretType = Field(
        default=SecretType.GENERIC,
        description="Secret type stored for the seeded credential.",
    )
    data: dict[str, str] = Field(
        default_factory=dict,
        description="Secret key/value pairs to store in the credential store.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional credential metadata stored alongside the secret values.",
    )


class SeededIntegrationConnectionConfig(BaseModel):
    """A startup-seeded integration connection."""

    id: str | None = Field(
        default=None,
        description=(
            "Optional fixed integration connection ID. When omitted, Volundr "
            "derives a stable UUID from the seeded connection fields."
        ),
    )
    owner_type: str = Field(
        default="user",
        description="Credential/integration owner type, usually 'user' in mini mode.",
    )
    owner_id: str = Field(
        default="dev-user",
        description="Owner receiving the seeded integration connection.",
    )
    integration_type: IntegrationType = Field(
        description="Category of integration being seeded.",
    )
    adapter: str = Field(
        description="Fully-qualified adapter path for the integration connection.",
    )
    credential_name: str = Field(
        description="Credential name referenced by the integration connection.",
    )
    slug: str = Field(
        default="",
        description="Catalog slug for the integration definition, e.g. 'telegram'.",
    )
    enabled: bool = Field(
        default=True,
        description="Whether the seeded connection starts enabled.",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-specific connection config.",
    )
    credential: SeededIntegrationCredentialConfig | None = Field(
        default=None,
        description="Optional credential payload to seed before creating the connection.",
    )


class FeatureModuleConfig(BaseModel):
    """A single feature module definition.

    Each entry defines a UI module that can be toggled on/off by admins
    and reordered/hidden by users. The ``key`` maps to a frontend component
    registered in the module registry.

    Example YAML::

        features:
          - key: users
            label: Users
            icon: Users
            scope: admin
            default_enabled: true
            order: 10
    """

    key: str = Field(description="Unique module identifier, e.g. 'users', 'storage'")
    label: str = Field(description="Display name shown in navigation")
    icon: str = Field(description="Lucide icon name, e.g. 'Users', 'HardDrive'")
    scope: str = Field(description="'admin' or 'user' — which page this module appears on")
    default_enabled: bool = Field(
        default=True,
        description="Whether this module is enabled by default for all users",
    )
    admin_only: bool = Field(
        default=False,
        description="Whether this module is only visible to admin users",
    )
    order: int = Field(
        default=0,
        description="Default sort order (lower = higher in nav)",
    )


def _default_feature_modules() -> list[FeatureModuleConfig]:
    """Return the built-in feature module catalog."""
    return [
        # Admin-scoped modules
        FeatureModuleConfig(
            key="users",
            label="Users",
            icon="Users",
            scope="admin",
            default_enabled=True,
            admin_only=True,
            order=10,
        ),
        FeatureModuleConfig(
            key="tenants",
            label="Tenants",
            icon="Building2",
            scope="admin",
            default_enabled=True,
            admin_only=True,
            order=20,
        ),
        FeatureModuleConfig(
            key="storage",
            label="Storage",
            icon="HardDrive",
            scope="admin",
            default_enabled=True,
            admin_only=True,
            order=30,
        ),
        FeatureModuleConfig(
            key="resources",
            label="Resources",
            icon="Cpu",
            scope="admin",
            default_enabled=True,
            admin_only=True,
            order=40,
        ),
        FeatureModuleConfig(
            key="feature-management",
            label="Features",
            icon="ToggleLeft",
            scope="admin",
            default_enabled=True,
            admin_only=True,
            order=50,
        ),
        # Session-scoped modules (main page panels)
        FeatureModuleConfig(
            key="chat",
            label="Chat",
            icon="MessageSquare",
            scope="session",
            default_enabled=True,
            order=10,
        ),
        FeatureModuleConfig(
            key="terminal",
            label="Terminal",
            icon="Terminal",
            scope="session",
            default_enabled=True,
            order=20,
        ),
        FeatureModuleConfig(
            key="code",
            label="Code",
            icon="Code",
            scope="session",
            default_enabled=True,
            order=30,
        ),
        FeatureModuleConfig(
            key="files",
            label="Files",
            icon="FolderOpen",
            scope="session",
            default_enabled=True,
            order=40,
        ),
        FeatureModuleConfig(
            key="diffs",
            label="Diffs",
            icon="GitCompareArrows",
            scope="session",
            default_enabled=True,
            order=50,
        ),
        FeatureModuleConfig(
            key="chronicles",
            label="Chronicles",
            icon="ScrollText",
            scope="session",
            default_enabled=True,
            order=60,
        ),
        FeatureModuleConfig(
            key="logs",
            label="Logs",
            icon="FileText",
            scope="session",
            default_enabled=True,
            order=70,
        ),
        # User-scoped modules
        FeatureModuleConfig(
            key="tokens",
            label="Access Tokens",
            icon="ShieldCheck",
            scope="user",
            default_enabled=True,
            order=5,
        ),
        FeatureModuleConfig(
            key="credentials",
            label="Credentials",
            icon="KeyRound",
            scope="user",
            default_enabled=True,
            order=10,
        ),
        FeatureModuleConfig(
            key="workspaces",
            label="Workspaces",
            icon="HardDrive",
            scope="user",
            default_enabled=True,
            order=20,
        ),
        FeatureModuleConfig(
            key="integrations",
            label="Integrations",
            icon="Link2",
            scope="user",
            default_enabled=True,
            order=30,
        ),
        FeatureModuleConfig(
            key="ting-connections",
            label="Ting Connections",
            icon="Compass",
            scope="user",
            default_enabled=True,
            order=35,
        ),
        FeatureModuleConfig(
            key="appearance",
            label="Appearance",
            icon="Palette",
            scope="user",
            default_enabled=True,
            order=40,
        ),
        FeatureModuleConfig(
            key="layout",
            label="Layout",
            icon="LayoutDashboard",
            scope="user",
            default_enabled=True,
            order=50,
        ),
    ]


class PATConfig(BaseModel):
    """Personal access token configuration."""

    token_issuer_adapter: str = Field(
        default="niuu.adapters.memory_token_issuer.MemoryTokenIssuer",
        description="Fully-qualified class path for the token issuer adapter.",
    )
    token_issuer_kwargs: dict = Field(
        default_factory=dict,
        description="Kwargs passed to the token issuer adapter constructor.",
    )
    ttl_days: int = Field(
        default=365,
        description="Default PAT lifetime in days.",
    )
    revocation_cache_ttl: float = Field(
        default=300.0,
        description="Seconds to cache valid-token lookups before re-checking the DB.",
    )
    revoked_cache_ttl: float = Field(
        default=60.0,
        description="Seconds to cache revoked-token lookups (shorter for faster propagation).",
    )


class AuthDiscoveryConfig(BaseModel):
    """Public auth discovery configuration for CLI and external clients.

    These values are exposed via the unauthenticated /auth/config endpoint
    so CLI clients can auto-discover OIDC settings.

    Example YAML::

        auth_discovery:
          issuer: "https://keycloak.niuu.world/realms/volundr"
          cli_client_id: "volundr-cli"
          scopes: "openid profile email"
    """

    issuer: str = Field(default="", description="OIDC issuer URL")
    cli_client_id: str = Field(default="volundr-cli", description="OIDC client ID for CLI clients")
    scopes: str = Field(default="openid profile email", description="OIDC scopes")


class GitHubWebhookConfig(BaseModel):
    """GitHub webhook receiver configuration."""

    secret: str | None = Field(
        default=None,
        description="HMAC-SHA256 secret for validating X-Hub-Signature-256 header.",
    )
    enabled: bool = Field(
        default=False,
        description="Enable GitHub webhook ingestion endpoint.",
    )
    rate_limit_per_minute: int = Field(
        default=100,
        ge=1,
        description="Maximum number of webhook events accepted per minute.",
    )


class WebhooksConfig(BaseModel):
    """Webhook ingestion configuration."""

    github: GitHubWebhookConfig = Field(default_factory=GitHubWebhookConfig)


class RavnConfig(BaseModel):
    """Ravn agent runtime configuration."""

    persona_source: PersonaSourceConfig = Field(
        default_factory=PersonaSourceConfig,
        description="Persona configuration source adapter.",
    )


class LinearConfig(BaseModel):
    """Linear issue tracker configuration."""

    enabled: bool = Field(default=False)
    api_key: str | None = Field(default=None)


class GitConfig(BaseModel):
    """Git provider configuration (extends niuu.config.GitConfig with Volundr-specific fields)."""

    github: GitHubConfig = Field(default_factory=GitHubConfig)
    gitlab: GitLabConfig = Field(default_factory=GitLabConfig)
    validate_on_create: bool = Field(default=True)
    workflow: GitWorkflowConfig = Field(default_factory=GitWorkflowConfig)


class TelegramIngressConfig(BaseModel):
    """Toggle for the Volundr-side Telegram update poller.

    Volundr's TelegramIngressService runs ``getUpdates`` long-polling on every
    enabled MESSAGING integration to route inbound Telegram messages into
    Skuld session rooms. Telegram allows only one active poller per bot
    token, so this conflicts with Ting's polling shim (``telegram.polling``)
    when both target the same bot. Disable here when Ting's shim is the
    intended consumer (``./start-dev`` solo dev). Defaults to True for
    backwards compatibility with deployed environments that rely on the
    in-session reply feature.
    """

    enabled: bool = Field(default=True)


class VolundrBifrostConfig(BifrostConfig):
    """Volundr-facing Bifrost dependency configuration."""

    url: str = Field(
        default="http://localhost:8080",
        description="Base URL for the mounted Bifrost API host.",
    )
    timeout_seconds: float = Field(
        default=10.0,
        description="HTTP timeout for Bifrost catalog calls.",
    )
    catalog_refresh_interval_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Interval between successful Bifrost catalog refreshes.",
    )
    auth: HttpAuthAdapterConfig = Field(default_factory=HttpAuthAdapterConfig)


class ObservatoryGuildConfig(BaseModel):
    """Guild dependency config consumed by the host-mounted Observatory app."""

    url: str = Field(
        default="http://localhost:8080",
        description="Base URL for the mounted Guild/niuu API host.",
    )
    timeout_seconds: float = Field(
        default=10.0,
        description="HTTP timeout for Observatory Guild discovery calls.",
    )
    auth: HttpAuthAdapterConfig = Field(default_factory=HttpAuthAdapterConfig)


class AgentDirectoryConfig(BaseModel):
    """Local card resolution and Guild fan-out bounds for the Agent Directory."""

    instance_id: str = Field(
        default="local-observatory",
        min_length=1,
        description="Stable identity of this Observatory source.",
    )
    cluster_id: str = Field(
        default="",
        description="Cluster identity used when discovery records omit placement.",
    )
    card_timeout_seconds: float = Field(
        default=4.0,
        gt=0,
        description="Timeout for Agent Card and signature-key retrieval.",
    )
    card_cache_ttl_seconds: float = Field(
        default=300.0,
        ge=0,
        description="Fallback card cache TTL when the owning service omits Cache-Control.",
    )
    local_max_concurrency: int = Field(
        default=8,
        ge=1,
        description="Maximum concurrent Agent Card resolutions per local directory request.",
    )
    guild_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Timeout for each Guild-to-Observatory directory request.",
    )
    guild_max_concurrency: int = Field(
        default=8,
        ge=1,
        description="Maximum concurrent Observatory fan-out requests from Guild.",
    )
    signature_algorithms: list[str] = Field(
        default_factory=lambda: ["ES256", "ES384", "RS256", "RS384", "PS256", "EdDSA"],
        min_length=1,
        description="Accepted Agent Card JWS algorithms.",
    )
    authenticated_card_origins: list[str] = Field(
        default_factory=list,
        description="HTTP(S) origins trusted to receive caller authentication for card retrieval.",
    )


class ObservatoryConfig(BaseModel):
    """Observatory plugin configuration."""

    guild: ObservatoryGuildConfig = Field(default_factory=ObservatoryGuildConfig)
    discovery: list[DynamicAdapterConfig] = Field(default_factory=list)
    directory: AgentDirectoryConfig = Field(default_factory=AgentDirectoryConfig)


class Settings(BaseSettings):
    """Application settings.

    Loads configuration from YAML file with environment variable overrides.

    YAML file locations (first found wins):
    - ./config.yaml
    - /etc/volundr/config.yaml

    Environment variable overrides use double underscore for nesting:
    - DATABASE__HOST=myhost -> settings.database.host
    - GIT__VALIDATE_ON_CREATE=false -> settings.git.validate_on_create
    """

    model_config = SettingsConfigDict(
        yaml_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    server_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("server_host", "NIUU_SERVER_HOST"),
        description="Internal host used by locally spawned session brokers.",
    )
    server_public_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices(
            "server_public_host",
            "NIUU_SERVER_PUBLIC_HOST",
            "NIUU_SERVER_HOST",
        ),
        description="Host published in browser-facing local session endpoints.",
    )
    server_port: int = Field(
        default=8080,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("server_port", "NIUU_SERVER_PORT"),
        description="Port of the shared Niuu host used by local session brokers.",
    )
    openshell_internal_gateway_url: str = Field(
        default="http://openshell.openshell.svc.cluster.local:8080",
        validation_alias=AliasChoices(
            "openshell_internal_gateway_url",
            "OPENSHELL_INTERNAL_GATEWAY_URL",
        ),
        description="Internal OpenShell gateway URL used for server-side session proxying.",
    )
    openshell_gateway_endpoint: str = Field(
        default="openshell.openshell.svc.cluster.local:8080",
        validation_alias=AliasChoices(
            "openshell_gateway_endpoint",
            "OPENSHELL_GATEWAY_ENDPOINT",
        ),
        description="OpenShell gRPC gateway endpoint forwarded to its pod-manager adapter.",
    )
    openshell_gateway_public_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "openshell_gateway_public_url",
            "OPENSHELL_GATEWAY_PUBLIC_URL",
        ),
        description="Browser-reachable OpenShell gateway URL.",
    )
    openshell_oidc_token_url: str = Field(
        default="https://keycloak.niuu.world/realms/volundr/protocol/openid-connect/token",
        validation_alias=AliasChoices(
            "openshell_oidc_token_url",
            "OPENSHELL_OIDC_TOKEN_URL",
        ),
        description="OIDC token endpoint used for OpenShell client credentials.",
    )
    openshell_oidc_client_id: str = Field(
        default="openshell-volundr-agent",
        validation_alias=AliasChoices(
            "openshell_oidc_client_id",
            "OPENSHELL_OIDC_CLIENT_ID",
        ),
        description="OIDC client id used for OpenShell client credentials.",
    )
    openshell_oidc_client_secret: str = Field(
        default="",
        exclude=True,
        repr=False,
        validation_alias=AliasChoices(
            "openshell_oidc_client_secret",
            "OPENSHELL_OIDC_CLIENT_SECRET",
        ),
        description="OIDC client secret; prefer pod_manager.secret_kwargs_env.",
    )
    cors: CorsConfig = Field(default_factory=CorsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    pod_manager: PodManagerConfig = Field(default_factory=PodManagerConfig)
    resident_runtimes: ResidentRuntimesConfig = Field(default_factory=ResidentRuntimesConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    niuu: InstanceRegistryConfig = Field(default_factory=InstanceRegistryConfig)
    chronicle: ChronicleConfig = Field(default_factory=ChronicleConfig)
    archive_store: ArchiveStoreConfig = Field(default_factory=ArchiveStoreConfig)
    event_pipeline: EventPipelineConfig = Field(default_factory=EventPipelineConfig)
    session_liveness: SessionLivenessConfig = Field(default_factory=SessionLivenessConfig)
    replay: ReplayConfig = Field(default_factory=ReplayConfig)
    sleipnir: SleipnirConfig = Field(default_factory=SleipnirConfig)
    push: PushNotificationConfig = Field(default_factory=PushNotificationConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    authorization: AuthorizationConfig = Field(default_factory=AuthorizationConfig)
    credential_store: CredentialStoreConfig = Field(default_factory=CredentialStoreConfig)
    codex_credential_broker: DynamicAdapterConfig = Field(
        default_factory=_default_codex_credential_broker,
        description=(
            "Configured Codex token broker. Local mode defaults to the disabled adapter so "
            "the host Codex login remains authoritative."
        ),
    )
    credential_enrollment_runner: DynamicAdapterConfig = Field(
        default_factory=_default_credential_enrollment_runner,
        description="Trusted interactive-login runner, independent of the session pod manager.",
    )
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    secret_injection: SecretInjectionConfig = Field(default_factory=SecretInjectionConfig)
    resource_provider: ResourceProviderConfig = Field(default_factory=ResourceProviderConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    webhooks: WebhooksConfig = Field(default_factory=WebhooksConfig)
    linear: LinearConfig = Field(default_factory=LinearConfig)
    pat: PATConfig = Field(default_factory=PATConfig)
    workload_identity: WorkloadIdentityConfig = Field(default_factory=WorkloadIdentityConfig)
    auth_discovery: AuthDiscoveryConfig = Field(default_factory=AuthDiscoveryConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    oauth: OAuthConfig = Field(default_factory=OAuthConfig)
    provisioning: ProvisioningConfig = Field(default_factory=ProvisioningConfig)
    permission_auto_approval: PermissionAutoApprovalConfig = Field(
        default_factory=PermissionAutoApprovalConfig,
        description="Server-side allow/deny policy for permission request auto approvals.",
    )
    local_git: LocalGitConfig = Field(default_factory=LocalGitConfig)
    local_mounts: LocalMountsConfig = Field(default_factory=LocalMountsConfig)
    external_sessions: ExternalSessionsConfig = Field(default_factory=ExternalSessionsConfig)
    telegram_ingress: TelegramIngressConfig = Field(default_factory=TelegramIngressConfig)
    session_contributors: list[SessionContributorConfig] = Field(default_factory=list)
    ravn_flock_image: str = Field(
        default="",
        description=(
            "Optional image used for auto-wired Ravn flock sidecars. "
            "When empty, the contributor's built-in default is used."
        ),
    )
    ravn_flock_init_writer_image: str = Field(
        default="",
        description=(
            "Optional image used by Ravn flock init containers that write per-persona "
            "config files. When empty, the contributor's built-in default is used."
        ),
    )
    session_definitions: dict[str, SessionDefinitionConfig] = Field(
        default_factory=default_session_definitions,
        description="Session definitions keyed by name (e.g. skuldClaude, skuldCodex).",
    )
    bifrost: VolundrBifrostConfig = Field(default_factory=VolundrBifrostConfig)
    default_definition: str = Field(
        default="skuldClaude",
        description="Fallback definition key when no explicit definition is specified.",
    )
    launch_specs: list[LaunchSpecConfig] = Field(
        default_factory=_default_launch_specs,
        description="System-scope launch specs preloaded into the launch catalog.",
    )
    mcp_servers: list[MCPServerEntry] = Field(default_factory=list)
    features: list[FeatureModuleConfig] = Field(
        default_factory=_default_feature_modules,
        description="Feature module catalog — defines available UI modules.",
    )
    ravn: RavnConfig = Field(default_factory=RavnConfig)
    observatory: ObservatoryConfig = Field(default_factory=ObservatoryConfig)

    @model_validator(mode="after")
    def _merge_built_in_session_definitions(self) -> "Settings":
        """Keep built-in session definitions unless config explicitly overrides them."""
        self.session_definitions = merge_session_definitions(self.session_definitions)
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize settings sources.

        Order (first wins):
        1. init_settings - explicit constructor arguments
        2. env_settings - environment variables
        3. yaml - YAML config file
        4. file_secret_settings - /run/secrets files
        """
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=_config_paths()),
            file_secret_settings,
        )
