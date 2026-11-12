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
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from bifrost.config import BifrostConfig
from niuu.adapters.notifications.integrations import DEFAULT_INTEGRATION_SINKS
from niuu.config import (
    CorsConfig,
    DynamicAdapterConfig,
    GitHubConfig,
    GitHubInstance,
    GitLabConfig,
    GitLabInstance,
    HttpAuthAdapterConfig,
    InstanceRegistryConfig,
    ProvenanceConfig,
)
from niuu.config_models import (
    DatabaseConfig,
    GuildMembershipConfig,
    SessionDefinitionConfig,
    SessionPreviewConfig,
    WorkloadIdentityConfig,
    default_session_definitions,
)
from niuu.domain.delivery import AcceptancePolicy
from niuu.domain.notifications import (
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    NotificationKind,
    NotificationSeverity,
    NotificationSource,
)
from niuu.domain.observability import ObservabilityConfig
from niuu.domain.presented_files import (
    PRESENTED_FILE_DEFAULT_MAX_BYTES,
    PRESENTED_FILE_DEFAULT_READ_CHUNK_BYTES,
)
from niuu.domain.tenancy import DEFAULT_PLATFORM_TENANT_ID
from niuu.forge_mcp.models import ForgeMcpGrant
from niuu.mesh.config import MeshSettings
from personas.config import PersonaSourceConfig
from skuld.tool_result_preview import PREVIEW_EDGE_LIMIT, PREVIEW_MAX_EDGE, PREVIEW_MIN_EDGE
from volundr.compute.config import ComputeConfig
from volundr.domain.mcp_hosts import normalize_internal_host_pattern
from volundr.domain.model_gateway import MODEL_GATEWAY_TOKEN_ENV
from volundr.domain.models import (
    IntegrationType,
    ResidentBackend,
    ResidentCapability,
    ResidentEngine,
    SecretType,
    require_workload_type_available,
)
from volundr.domain.notifications import (
    INTEGRATION_SINK,
    MAX_SINK_NAME_CHARS,
    SINK_NAME_PATTERN,
    NotificationQuietHours,
    NotificationRateLimit,
)
from volundr.domain.presented_files import PresentedFilesAccess, validate_public_base_url
from volundr.domain.push import PushTokenKind

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


class DeliveryConfig(BaseModel):
    """Explicit deployment policy for evidence-backed developer delivery."""

    enabled: bool = False
    authenticator: DynamicAdapterConfig = Field(default_factory=DynamicAdapterConfig)
    workstreams: DynamicAdapterConfig = Field(default_factory=DynamicAdapterConfig)
    authorizer: DynamicAdapterConfig = Field(default_factory=DynamicAdapterConfig)
    forge: DynamicAdapterConfig = Field(
        default_factory=lambda: DynamicAdapterConfig(
            adapter="volundr.adapters.outbound.user_delivery_forge.UserDeliveryForgeProvider"
        )
    )
    producer_id: str = "forge-service"
    workstream_producer_id: str = "workstream-runner"
    trusted_producers: tuple[str, ...] = ()
    policies: dict[str, AcceptancePolicy] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_explicit_delivery_configuration(self):
        if self.enabled:
            if (
                not self.authenticator.adapter
                or not self.workstreams.adapter
                or not self.forge.adapter
            ):
                raise ValueError(
                    "Enabled delivery requires authenticator, workstreams, and forge adapters"
                )
            if not self.trusted_producers or not self.policies:
                raise ValueError(
                    "Enabled delivery requires pinned producers and acceptance policies"
                )
            if not self.producer_id.strip() or not self.workstream_producer_id.strip():
                raise ValueError("Enabled delivery requires forge and workstream producer IDs")
            if not self.authorizer.adapter:
                raise ValueError("Enabled delivery requires an execution authorizer adapter")
        return self


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
    startup_poll_seconds: float = Field(
        default=2.0,
        gt=0,
        description=(
            "How often Forge reads a starting session's launch stages from its runtime "
            "backend (pod conditions and events, VM state) while the session starts."
        ),
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


class VolundrObservabilityConfig(ObservabilityConfig):
    """OpenTelemetry settings with Volundr's stable service identity."""

    service_name: str = Field(default="volundr")


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
    runtime_backend: str | None = Field(
        default=None,
        description="Explicit contributor backend identity; VM deployments use vm.",
    )
    room_role_source: Literal["deployment", "remote"] = Field(
        default="deployment",
        description=(
            "ws_auth.room_role_source Volundr renders into this backend's session "
            "pods (kubernetes only — see charts/skuld/values.yaml's wsAuth and "
            "volundr/adapters/outbound/contributors/room_role.py). 'deployment' "
            "(the default): unchanged pre-session_participants behavior — a caller "
            "reaching the pod at all is owner, and session_participants invites are "
            "refused with 409 for this backend (see rest_session_participants.py's "
            "REMOTE_CAPABLE_RUNTIME_BACKENDS). 'remote': pods are deployed with "
            "ws_auth.room_role_source: remote and a wsAuth.room_role_remote adapter "
            "(RemoteAuthorizationAdapter) that asks Forge for each caller's grant, "
            "so session_participants invites are honoured and the 409 is lifted. "
            "Also requires wsAuth.enforce_ownership: false (the chart's Helm render "
            "fails otherwise — the ext_authz sidecar's owner/admin-only 'start' gate "
            "would block every participant before a remote lookup ever ran). This is "
            "a property of the WHOLE deployment, not a per-session choice — flipping "
            "it changes every future session pod's trust boundary, so it must be set "
            "deliberately, verified in a non-production cluster first, and never "
            "enabled by inference from other settings."
        ),
    )
    room_role_cache_ttl_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "Rendered as room_role_remote.kwargs.cache_ttl_seconds when "
            "room_role_source is 'remote' — how long RemoteAuthorizationAdapter "
            "caches a resolved role before re-asking Forge, bounding how quickly "
            "a revoked or demoted grant takes effect on an already-open connection."
        ),
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


#: Engine value of the removed Ravn resident runtime, named only to refuse it.
REMOVED_RAVN_RESIDENT_ENGINE = "ravn"


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

    @field_validator("engine", mode="before")
    @classmethod
    def _refuse_removed_engines(cls, value: Any) -> Any:
        """Name the remedy for a profile still configured with a removed engine."""
        if str(value).strip().lower() == REMOVED_RAVN_RESIDENT_ENGINE:
            raise ValueError(
                "the ravn resident engine was removed; set this profile's engine to openclaw "
                "or hermes, or delete the profile (charts/volundr residentRuntimeProfiles)"
            )
        return value


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
    a2a_public_base_url: str = Field(
        default="",
        description=(
            "Public origin under which residents are reached over A2A "
            "(/api/v1/forge/resident-runtimes/{id}/a2a), e.g. the Guild that proxies "
            "this target. When set, each A2A-capable resident lists its Agent Card and "
            "A2A endpoint so the Agent Directory can discover it; empty publishes none."
        ),
    )
    a2a_reply_timeout_seconds: float = Field(
        default=45.0,
        ge=0,
        description=(
            "How long an A2A SendMessage waits for a resident's reply before returning "
            "the task WORKING for the caller to poll. Keep it under the proxy timeouts."
        ),
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

    @field_validator("workload_type")
    @classmethod
    def _workload_type_still_exists(cls, value: str) -> str:
        return require_workload_type_available(value)


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
            model="gpt-5.6-terra",
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
    """OpenTelemetry event *sink* configuration — GenAI spans from durable
    ``SessionEvent`` rows, one span per already-recorded event
    (``OtelEventSink``, wired via ``event_pipeline.otel``).

    Distinct from top-level ``observability`` (``VolundrObservabilityConfig``,
    on ``Settings.observability``), which drives the shared
    ``niuu.observability`` facade: the FastAPI/httpx auto-instrumentation and
    every ``get_observability()`` call site across the codebase (Ravn LLM
    adapters, Bifröst, session contributors, ...). Two separate OTel
    pipelines, each with its own ``TracerProvider``/exporter, because they
    serve different questions:

    * ``observability`` (this process's server/client spans) answers "what
      did this request do, and in what larger trace" — real-time, one trace
      id follows the work end to end.
    * ``event_pipeline.otel`` (this sink) answers "replay this session's
      already-recorded event history as spans" — after the fact, from
      Postgres, keyed by ``session_id``, not tied to any live trace context.

    Not consolidated into one pipeline: they run on different triggers (live
    request vs. durable event replay) and would need one to synthesize
    context for the other's spans to nest correctly, which neither
    currently does. If you only want live traces, ``observability.enabled``
    alone is enough — leave this at its default (disabled).

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


DEFAULT_PUSH_ADAPTER = "volundr.adapters.outbound.push_channels.LoggingNotificationChannel"


class ApnsConfig(BaseModel):
    """Apple Push Notification service credentials (token-based auth).

    ``private_key_path`` is the ``AuthKey_<KEYID>.p8`` file from the Apple Developer
    portal. It is read once at startup; a missing or wrong key stops the start.
    One key serves the sandbox and the production host.
    """

    team_id: str = Field(default="", description="Apple Developer team id (the JWT issuer).")
    key_id: str = Field(default="", description="Id of the APNs auth key (the JWT kid).")
    bundle_id: str = Field(
        default="",
        description="The app's bundle id: the APNs topic for a token registered without one.",
    )
    private_key_path: str = Field(
        default="", description="Path of the .p8 signing key file (never its contents)."
    )
    token_ttl_seconds: int = Field(
        default=3000,
        ge=1200,
        le=3540,
        description=(
            "How long one provider JWT is reused. APNs refuses a JWT older than an "
            "hour and throttles a provider that signs more often than every 20 minutes."
        ),
    )
    request_timeout_seconds: float = Field(
        default=10.0, gt=0, description="Timeout for one request to APNs."
    )
    production_url: str = Field(
        default="https://api.push.apple.com",
        description="APNs host for tokens of TestFlight and App Store installs.",
    )
    sandbox_url: str = Field(
        default="https://api.sandbox.push.apple.com",
        description="APNs host for tokens of development installs.",
    )


class PushAlertsConfig(BaseModel):
    """Which Guild notifications alert the owner's devices, and how.

    This is the gateway's one alert rule, applied per owner. Its rate limit and
    quiet hours mean what they mean on a delivery rule (``notifications`` rules).
    """

    enabled: bool = Field(default=True, description="Send alert pushes.")
    kinds: list[NotificationKind] = Field(
        default_factory=lambda: [
            NotificationKind.MILESTONE,
            NotificationKind.DECISION,
            NotificationKind.ATTENTION,
            NotificationKind.ERROR,
        ],
        description="Notification kinds that alert. Others still reach the Live Activity.",
    )
    min_severity: NotificationSeverity = Field(
        default=NotificationSeverity.INFO, description="Lowest severity that alerts."
    )
    sources: list[NotificationSource] = Field(
        default_factory=list, description="Sources that alert; empty means any."
    )
    attention_kinds: list[NotificationKind] = Field(
        default_factory=lambda: [
            NotificationKind.ATTENTION,
            NotificationKind.DECISION,
            NotificationKind.ERROR,
        ],
        description=(
            "Kinds that need the user (a failure that stops work breaks through like a "
            "question). Only they are time-sensitive (others are active), and "
            "only they carry mutable-content, the attention marker and the agent identity, so "
            "the app's notification service extension runs on them (LEX-1186)."
        ),
    )
    low_priority_kinds: list[NotificationKind] = Field(
        default_factory=list,
        description="Kinds APNs may deliver when power allows (apns-priority 5).",
    )
    body_chars: int = Field(default=280, ge=1, description="Longest alert body.")
    expiration_seconds: int = Field(
        default=3600, ge=0, description="How long APNs keeps trying a device that is offline."
    )
    max_age_seconds: float = Field(
        default=900.0,
        gt=0,
        description=(
            "A notification older than this when the gateway reads it is not alerted "
            "(after the gateway or a node was unreachable)."
        ),
    )
    rate_limit: NotificationRateLimit | None = Field(
        default_factory=lambda: NotificationRateLimit(max_count=30, window_seconds=3600),
        description="At most max_count alerts per owner in any window_seconds; null disables.",
    )
    quiet_hours: NotificationQuietHours | None = Field(
        default=None,
        description=(
            "A daily window (start, end, timezone) in which only notifications at or "
            "above allow_min_severity alert."
        ),
    )
    category: str = Field(
        default="FORGE_ACTIVITY", description="The alert's category (aps.category)."
    )
    sound: str = Field(default="default", description="The alert's sound; empty is silent.")
    deep_link_base: str = Field(
        default="lexichat://activity",
        min_length=1,
        description="The app link a tap opens; the notification id and its session are appended.",
    )
    session_subtitle: bool = Field(
        default=True,
        description="Show the session name as the alert's subtitle when the title lacks it.",
    )
    batch_size: int = Field(default=20, ge=1, description="Alerts claimed per round.")
    lease_seconds: float = Field(
        default=60.0, gt=0, description="How long a claimed alert is leased to one worker."
    )
    max_attempts: int = Field(
        default=5, ge=1, description="Attempts before an alert APNs cannot take is given up."
    )
    backoff_base_seconds: float = Field(
        default=15.0, gt=0, description="First retry delay; doubles per attempt."
    )
    backoff_max_seconds: float = Field(default=600.0, gt=0, description="Longest retry delay.")

    @model_validator(mode="after")
    def _ordered_backoff(self) -> "PushAlertsConfig":
        if self.backoff_base_seconds > self.backoff_max_seconds:
            raise ValueError("alerts.backoff_base_seconds must not exceed backoff_max_seconds")
        return self


class PushLiveActivityStartConfig(BaseModel):
    """Starting the Notifications Live Activity by push when none is running."""

    enabled: bool = Field(
        default=False,
        description=(
            "Start the Live Activity on a new notification when the owner has a "
            "push-to-start token and no running activity."
        ),
    )
    min_interval_seconds: float = Field(
        default=3600.0, gt=0, description="At most one start per token in this interval."
    )


class PushLiveActivityConfig(BaseModel):
    """Live Activity pushes: the newest Guild notifications on the Lock Screen."""

    enabled: bool = Field(default=True, description="Send Live Activity pushes.")
    priority: int = Field(
        default=10, ge=1, le=10, description="apns-priority of a Live Activity push (5 or 10)."
    )
    stale_seconds: int = Field(
        default=28800, ge=1, description="How long after a push iOS shows the content as current."
    )
    relevance_score: float = Field(
        default=10,
        ge=0,
        description=(
            "aps.relevance-score on every Live Activity push (start and update): which of "
            "the app's own activities iOS shows in the Dynamic Island. The app's scale "
            "(LEX-1324/LEX-1353): a live call 100, notifications 10, so a call always wins."
        ),
    )
    start: PushLiveActivityStartConfig = Field(default_factory=PushLiveActivityStartConfig)


class PushWidgetsConfig(BaseModel):
    """WidgetKit reload pushes. iOS budgets them, so they are spaced per token."""

    enabled: bool = Field(default=True, description="Send widget reload pushes.")
    min_interval_seconds: float = Field(
        default=120.0,
        gt=0,
        description=(
            "At most one reload per widget token in this interval; a reload held "
            "back is sent when the interval ends, so the last change is never dropped."
        ),
    )
    priority: int = Field(
        default=10, ge=1, le=10, description="apns-priority of a widget reload (5 or 10)."
    )


class PushBackgroundConfig(BaseModel):
    """Silent background pushes to the app's alert token.

    They let iOS wake the app, which then reads every server it knows (a server
    only knows its own Guild). iOS delivers only a few per hour, so they are spaced
    per token.
    """

    enabled: bool = Field(default=True, description="Send background pushes.")
    min_interval_seconds: float = Field(
        default=900.0,
        gt=0,
        description=(
            "At most one background push per alert token in this interval; one held "
            "back is sent when the interval ends."
        ),
    )
    priority: int = Field(
        default=5,
        ge=1,
        le=10,
        description="apns-priority of a background push. APNs refuses 10 for this type.",
    )


class PushCallsConfig(BaseModel):
    """Rings: a resident's ``call.request`` sent to its owner's phone as a VoIP push
    (``POST /api/v1/forge/push/call``, scope ``forge:push:call``).

    iOS cuts off an app that gets VoIP pushes without reporting a call, so only a
    validated call request is ever sent. There is no rate cap on rings.
    """

    enabled: bool = Field(
        default=True,
        description="Serve POST /push/call on the push gateway's node.",
    )
    ring_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description=(
            "apns-expiration is at most this far ahead (and never past the request's "
            "expiresAt): a ring that arrives late is worse than none."
        ),
    )


class PushGatewayConfig(BaseModel):
    """The push gateway: Apple pushes for every notification of this server's Guild.

    Any Forge server turns it on with configuration. A phone that uses several
    servers registers with each; each pushes for its own Guild.

    One node holds the APNs key (``push.apns``) and the registered devices. It
    reads every node's notification feed through the Guild API at ``guild_url`` and
    sends the alert, Live Activity and widget pushes for all of them, so no other
    node needs a key.
    """

    enabled: bool = Field(default=False, description="Run the push gateway on this node.")
    guild_url: str = Field(
        default="",
        description=(
            "Origin of the Guild API (its registry and its notification feed), "
            "e.g. http://127.0.0.1:8080."
        ),
    )
    reader_token_path: str = Field(
        default="",
        description=(
            "File holding the bearer token the gateway reads the Guild with. Empty "
            "sends no credential (a Guild with the AllowAll identity). On an "
            "authenticated Guild use reader_auth instead: the token must be a "
            "forge:notifications:deliver workload credential, the only one that reads "
            "every owner's and every org's notifications."
        ),
    )
    reader_auth: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The HttpAuthPort the gateway reads the Guild with, as {adapter, kwargs} "
            "(e.g. niuu.adapters.outbound.http_auth.WorkloadIdentityBearerTokenAuthAdapter "
            "with scopes: [forge:notifications:deliver]). Use this or reader_token_path."
        ),
    )
    request_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "Timeout for one read of one Guild node. A node that does not answer in "
            "time fails only its own read."
        ),
    )
    feedless_recheck_seconds: float = Field(
        default=3600.0,
        gt=0,
        description=(
            "How long a Guild node without a notifications feed (an older Forge answering "
            "404) is left alone before it is asked again; it is skipped quietly meanwhile."
        ),
    )
    node_retry_seconds: float = Field(
        default=30.0,
        ge=0,
        description="How long a node that did not answer is left alone before the next try.",
    )
    poll_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "How often the gateway reads the Guild feed. This node's own notifications "
            "wake it at once; another node's are pushed within this interval."
        ),
    )
    stream_enabled: bool = Field(
        default=False,
        description=(
            "Also listen to the Guild's live event stream, so another node's notification "
            "is pushed at once. It holds one streaming connection to guild_url: leave it "
            "off when the Guild runs in this same process, where an open stream delays a "
            "graceful shutdown."
        ),
    )
    stream_retry_seconds: float = Field(
        default=5.0, gt=0, description="Wait before reconnecting a lost event stream."
    )
    page_size: int = Field(default=50, ge=1, description="Notifications read per node per request.")
    feed_page_size: int = Field(
        default=20,
        ge=3,
        description="Newest notifications read per node to build the Live Activity content.",
    )
    catch_up_items: int = Field(
        default=10,
        ge=0,
        description="How many of a newly found node's latest notifications are read back.",
    )
    discovery_interval_seconds: float = Field(
        default=300.0, gt=0, description="How often the gateway looks for new Guild nodes."
    )
    retention_seconds: float = Field(
        default=604800.0, gt=0, description="How long settled alert rows are kept."
    )
    max_detail_chars: int = Field(
        default=500, ge=1, description="Longest delivery detail kept on an alert row."
    )
    public_api_url: str = Field(
        default="",
        description=(
            "Origin a phone reaches this Guild at, for image URLs in alerts. Empty "
            "leaves the image out of the alert."
        ),
    )
    origin_label: str = Field(
        default="",
        description="This server's display name, shown on a Live Activity it pushed.",
    )
    alerts: PushAlertsConfig = Field(default_factory=PushAlertsConfig)
    live_activity: PushLiveActivityConfig = Field(default_factory=PushLiveActivityConfig)
    widgets: PushWidgetsConfig = Field(default_factory=PushWidgetsConfig)
    background: PushBackgroundConfig = Field(default_factory=PushBackgroundConfig)
    calls: PushCallsConfig = Field(default_factory=PushCallsConfig)

    @model_validator(mode="after")
    def _needs_guild(self) -> "PushGatewayConfig":
        if self.enabled and not self.guild_url.strip():
            raise ValueError(
                "push.gateway.enabled needs push.gateway.guild_url (the Guild API origin)"
            )
        if self.reader_auth and self.reader_token_path.strip():
            raise ValueError(
                "push.gateway.reader_auth and push.gateway.reader_token_path both name the "
                "gateway's Guild credential: keep one"
            )
        if self.reader_auth and not str(self.reader_auth.get("adapter", "")).strip():
            raise ValueError(
                "push.gateway.reader_auth needs an adapter (a fully-qualified HttpAuthPort "
                "class path), with its kwargs"
            )
        unknown = set(self.reader_auth) - {"adapter", "kwargs"}
        if unknown:
            raise ValueError(
                "push.gateway.reader_auth takes adapter and kwargs only, not "
                + ", ".join(sorted(unknown))
            )
        return self


class PushNotificationConfig(BaseModel):
    """Apple push: the Guild push gateway, or the per-node push channel (optional).

    Two modes, both behind ``enabled``:

    * **Gateway** (``gateway.enabled``): this node pushes for every Guild node's
      notifications through APNs (``apns``): alerts, Live Activity, widgets and
      background pushes. The needs-input moment is alerted from its ``attention``
      notification, so no "Needs-input pushes" rule is given and the per-node
      channel below must be left at its default, here and on every other node of
      the Guild (the gateway holds back the alerts of a node that pushes its own).
    * **Per-node channel** (``adapter`` + ``kwargs``): the NotificationChannel
      that push sinks deliver through (the default adapter only logs). Every owner
      who registers a device gets a "Needs-input pushes" delivery rule targeting
      ``attention_sink``, so each session entering ``awaiting_input`` is pushed
      through the notification outbox (retried, recorded, quiet hours honoured).
      That needs ``notifications.enabled`` and a ``notifications.sinks`` entry
      named ``attention_sink``.

    Per-node channel YAML::

        push:
          enabled: true
          attention_sink: push
          adapter: "volundr.adapters.outbound.push_channels.ApnsNotificationChannel"
          kwargs:
            team_id: "ABCDE12345"
            key_id: "KEY1234567"
            bundle_id: "com.niuu.forge"
          secret_kwargs_env:
            private_key: "APNS_PRIVATE_KEY"

    The APNs key belongs to the app, so it is platform configuration (a Kubernetes
    Secret, e.g. from an ExternalSecret), not a credential-store record. Registered
    devices are per user: each app install is the owner's push-device integration,
    every token of it in the credential store.

    Gateway YAML::

        push:
          enabled: true
          apns:
            team_id: "ABCDE12345"
            key_id: "KEY1234567"
            bundle_id: "com.example.app"
            private_key_path: "/etc/volundr/secrets/AuthKey_KEY1234567.p8"
          gateway:
            enabled: true
            guild_url: "http://127.0.0.1:8080"
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Enable push: the gateway when gateway.enabled; else every owner who registers "
            "a device gets a delivery rule for sessions that need them (needs "
            "notifications.enabled)."
        ),
    )
    attention_sink: str = Field(
        default="push",
        description=(
            "The notifications.sinks entry the needs-input push rule delivers through "
            "(per-node mode; the gateway gives no such rule)."
        ),
    )
    adapter: str = Field(
        default=DEFAULT_PUSH_ADAPTER,
        description="Fully-qualified NotificationChannel class path (per-node mode).",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of kwarg names to env var names holding secret values.",
    )
    device_registry: Literal["auto", "postgres", "integrations"] = Field(
        default="auto",
        description=(
            "Kept for configurations that name it: push devices are always the owners' "
            "messaging integration connections, every token in the credential store and "
            "the gateway's state in device_tokens. 'postgres' is refused: that registry "
            "was merged into this one (its rows are moved at startup)."
        ),
    )
    apns: ApnsConfig = Field(default_factory=ApnsConfig)
    gateway: PushGatewayConfig = Field(default_factory=PushGatewayConfig)

    @model_validator(mode="after")
    def _one_mode(self) -> "PushNotificationConfig":
        if not self.gateway.enabled:
            return self
        if not self.enabled:
            raise ValueError(
                "push.gateway.enabled needs push.enabled: true (or disable the gateway)"
            )
        missing = [
            name
            for name in ("team_id", "key_id", "bundle_id", "private_key_path")
            if not getattr(self.apns, name).strip()
        ]
        if missing:
            raise ValueError("push.gateway.enabled needs push.apns." + ", push.apns.".join(missing))
        if self.adapter != DEFAULT_PUSH_ADAPTER or self.kwargs or self.secret_kwargs_env:
            raise ValueError(
                "push.adapter / push.kwargs configure the per-node needs-input push, which "
                "the gateway replaces: remove them, or disable push.gateway"
            )
        return self

    @model_validator(mode="after")
    def _one_registry(self) -> "PushNotificationConfig":
        if self.device_registry == "postgres":
            raise ValueError(
                "push.device_registry: postgres is gone: push devices are integration "
                "connections with their tokens in the credential store, and Forge moves the "
                "tokens device_tokens holds there at startup. Remove push.device_registry"
            )
        return self

    def pushes_alerts_itself(self) -> bool:
        """Whether this node pushes alerts through its own channel (per-node mode with a
        real adapter): a Guild push gateway then leaves this node's alerts to it."""
        return self.enabled and not self.gateway.enabled and self.adapter != DEFAULT_PUSH_ADAPTER

    def accepted_token_kinds(self) -> frozenset[PushTokenKind]:
        """The token kinds this node registers: every kind on the push gateway's node,
        alert tokens elsewhere (no other node pushes the rest)."""
        if self.enabled and self.gateway.enabled:
            return frozenset(PushTokenKind)
        return frozenset({PushTokenKind.ALERT})


class NotificationReplyReadyConfig(BaseModel):
    """The automatic ``reply_ready`` notification raised for each final reply.

    Off by default: a notification for every final reply buries the ones agents send on
    purpose. Agents report a finished task with their own ``milestone`` instead.
    """

    enabled: bool = Field(
        default=False,
        description="Record a reply_ready notification for every final assistant reply.",
    )
    title_chars: int = Field(
        default=120,
        ge=1,
        le=MAX_TITLE_CHARS,
        description="Title length (the reply's first line) before it is truncated.",
    )
    body_chars: int = Field(
        default=280,
        ge=0,
        le=MAX_BODY_CHARS,
        description="Length of the reply excerpt kept as the notification body.",
    )


class NotificationDispatcherConfig(BaseModel):
    """Outbox dispatcher: the background loop that delivers notifications to sinks."""

    enabled: bool = Field(
        default=True,
        description=(
            "Run the outbox dispatcher (needs notifications.enabled). It only sends what "
            "an owner rule selects, so it is idle until a rule exists."
        ),
    )
    poll_interval_seconds: float = Field(
        default=2.0, gt=0, description="Pause between outbox polls when nothing is due."
    )
    batch_size: int = Field(default=50, ge=1, description="Deliveries claimed per poll.")
    lease_seconds: float = Field(
        default=60.0,
        gt=0,
        description="How long a claim is held before another worker may re-claim it.",
    )
    send_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        description=(
            "Upper bound for one sink send. Must be shorter than lease_seconds; a send "
            "is only started while at least this much of its lease remains."
        ),
    )
    max_concurrent_sends: int = Field(
        default=8, ge=1, description="Sends in flight at once within one poll."
    )
    max_attempts: int = Field(
        default=8, ge=1, description="Attempts before a delivery is marked dead."
    )
    backoff_base_seconds: float = Field(
        default=5.0, gt=0, description="First retry delay; doubles on each attempt."
    )
    backoff_max_seconds: float = Field(
        default=900.0, gt=0, description="Upper bound for the retry delay."
    )
    backoff_jitter_ratio: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Random spread applied to each retry delay (0.2 = up to ±20%).",
    )
    default_rate_limit: NotificationRateLimit | None = Field(
        default_factory=lambda: NotificationRateLimit(max_count=60, window_seconds=3600),
        description=(
            "Rate limit for rules that set no config.rate_limit: at most max_count "
            "deliveries per rule in any window_seconds. Null disables the default."
        ),
    )
    max_error_chars: int = Field(
        default=2000, ge=1, description="Longest delivery error kept on the outbox row."
    )

    @model_validator(mode="after")
    def _ordered_timing(self) -> "NotificationDispatcherConfig":
        if self.backoff_base_seconds > self.backoff_max_seconds:
            raise ValueError("backoff_base_seconds must not exceed backoff_max_seconds")
        if self.send_timeout_seconds >= self.lease_seconds:
            raise ValueError("send_timeout_seconds must be shorter than lease_seconds")
        return self


class NotificationImagesConfig(BaseModel):
    """Copying images attached to notifications (``image`` on notify / submit).

    Forge copies each attached image once, in the background, and serves its own
    copy. A ``file_id`` image is read through the session's broker; a ``url`` image
    is fetched over https from a public host (see ``allow_private_networks``).
    """

    poll_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "Pause between polls for pending copies (e.g. left by a restart). A new "
            "image wakes the worker at once."
        ),
    )
    batch_size: int = Field(default=8, ge=1, description="Pending copies claimed per poll.")
    lease_seconds: float = Field(
        default=60.0,
        gt=0,
        description="How long a claimed copy is held before another worker may re-claim it.",
    )
    fetch_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        description="Upper bound for fetching one image. Must be shorter than lease_seconds.",
    )
    max_concurrent_fetches: int = Field(
        default=4, ge=1, description="Image fetches in flight at once."
    )
    max_redirects: int = Field(
        default=3, ge=0, description="Redirects followed for a url image (each hop is checked)."
    )
    allow_private_networks: bool = Field(
        default=False,
        description=(
            "Let url images resolve to private, loopback, link-local or shared "
            "(e.g. tailnet 100.64.0.0/10) addresses. Off: only public hosts are fetched."
        ),
    )
    thumbnail_max_side: int = Field(
        default=320, ge=1, description="Longest side of a stored thumbnail, in pixels."
    )
    thumbnail_jpeg_quality: int = Field(
        default=85, ge=1, le=95, description="JPEG quality for opaque thumbnails."
    )
    cache_max_age_seconds: int = Field(
        default=31_536_000,
        ge=0,
        description="Cache-Control max-age for served images; stored bytes never change.",
    )
    max_error_chars: int = Field(
        default=300, ge=1, description="Longest image failure reason kept and shown."
    )

    @model_validator(mode="after")
    def _ordered_timing(self) -> "NotificationImagesConfig":
        if self.fetch_timeout_seconds >= self.lease_seconds:
            raise ValueError("images.fetch_timeout_seconds must be shorter than lease_seconds")
        return self


class PresentedFilesConfig(BaseModel):
    """Durable presented files (control-plane F2).

    Forge keeps its own copy of every file a session presents, so the file still opens
    after the session stops. Each copy may also be linked by an absolute URL on an
    origin where access is controlled (``public_access``).

    Mini Forge on a tailnet (one Forge process, a local directory)::

        presented_files:
          enabled: true
          public_base_url: "http://thor.tail737f2a.ts.net:8380"
          max_total_bytes: 5368709120   # required when enabled (LEX-1514)
          store:
            adapter: "volundr.adapters.outbound.local_presented_files.LocalPresentedFileStore"
            root: "~/.niuu/presented-files"

    Cluster Forge behind the platform's OIDC (any number of replicas)::

        presented_files:
          enabled: true
          public_access: authenticated
          public_base_url: "https://yggdrasil.niuu.world"
          max_total_bytes: 5368709120
          store:
            adapter: "volundr.adapters.outbound.postgres_presented_files.PostgresPresentedFileStore"
            root: "/volundr/presented-files"   # a volume every Forge replica mounts
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Accept and serve durable copies. Off by default: the store needs a "
            "persistent directory (a local one for a single mini Forge process, or a "
            "volume every replica mounts with the Postgres store)."
        ),
    )
    public_access: PresentedFilesAccess = Field(
        default=PresentedFilesAccess.TAILNET,
        description=(
            "What controls access to public_base_url. 'tailnet': it must be a *.ts.net "
            "name or a Tailscale address. 'authenticated': an https origin where every "
            "request passes the platform's OIDC (Envoy); refused at start when Forge's "
            "identity adapter is the allow-all default."
        ),
    )
    public_base_url: str = Field(
        default="",
        description=(
            "Absolute http(s) base URL clients reach this Forge at, checked against "
            "public_access at start. Empty: presented files keep their relative route."
        ),
    )
    max_bytes: int = Field(
        default=PRESENTED_FILE_DEFAULT_MAX_BYTES,
        gt=0,
        description="Largest durable copy accepted (matches Skuld's max_presented_file_bytes).",
    )
    read_chunk_bytes: int = Field(
        default=PRESENTED_FILE_DEFAULT_READ_CHUNK_BYTES,
        gt=0,
        description=(
            "Bytes read per step when Forge streams a durable copy to a client: the "
            "memory one download holds at a time. Not a cap: every byte is served."
        ),
    )
    max_total_bytes: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Total bytes of durable copies this Forge keeps (LEX-1514). Required when "
            "enabled: it is bounded by the store's free space, which Forge checks at "
            "start. A copy that would pass it is refused (HTTP 507, present-file fails "
            "with the remedy) and counted; nothing is evicted."
        ),
    )
    store: dict[str, Any] = Field(
        default_factory=lambda: {
            "adapter": "volundr.adapters.outbound.local_presented_files.LocalPresentedFileStore",
            "root": "~/.niuu/presented-files",
        },
        description=(
            "Dynamic adapter for the store: 'adapter' class path plus its kwargs. The "
            "local store serves one Forge process; the Postgres store any number."
        ),
    )

    @model_validator(mode="after")
    def _valid_base_url(self) -> "PresentedFilesConfig":
        self.public_base_url = validate_public_base_url(self.public_base_url, self.public_access)
        return self

    @model_validator(mode="after")
    def _quota_when_enabled(self) -> "PresentedFilesConfig":
        if self.enabled and self.max_total_bytes is None:
            raise ValueError(
                "presented_files.enabled needs presented_files.max_total_bytes: the total "
                "bytes of copies this host may keep (for example 5368709120 for 5 GiB)"
            )
        return self


class NotificationsConfig(BaseModel):
    """Forge session notifications: the feed, the projection and external delivery.

    Nothing is delivered externally by default: a sink must be configured here (or
    an owner's messaging integration used) and an owner rule must select it.

    Example YAML::

        notifications:
          enabled: true
          reply_ready:
            enabled: false
            title_chars: 120
            body_chars: 280
          public_web_url: "https://forge.example.com"
          images:
            fetch_timeout_seconds: 20
            thumbnail_max_side: 320
          sinks:
            - name: ops-webhook
              label: Ops webhook
              adapter: "niuu.adapters.notifications.webhook.WebhookNotificationSink"
              url: "https://hooks.example.com/forge"
              secret_kwargs_env:
                secret: FORGE_OPS_WEBHOOK_SECRET
    """

    enabled: bool = Field(
        default=True,
        description="Project notifications and serve the notification API.",
    )
    reply_ready: NotificationReplyReadyConfig = Field(default_factory=NotificationReplyReadyConfig)
    default_page_size: int = Field(default=50, ge=1, description="Feed page size default.")
    max_page_size: int = Field(default=200, ge=1, description="Largest feed page allowed.")
    stale_after_seconds: int = Field(
        default=4 * 3600,
        gt=0,
        description=(
            "An unresolved attention, error or critical notification older than this whose "
            "session has posted a newer milestone is served as stale: the session moved "
            "on, so the alarm is probably over even though nothing resolved it (LEX-1442). "
            "It is display only: nothing is resolved or hidden."
        ),
    )
    dispatcher: NotificationDispatcherConfig = Field(default_factory=NotificationDispatcherConfig)
    images: NotificationImagesConfig = Field(default_factory=NotificationImagesConfig)
    sinks: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Named delivery sinks (dynamic adapters): each entry has 'name', optional "
            "'label', 'adapter' (fully-qualified NotificationSink class path), optional "
            "'secret_kwargs_env' (kwarg name -> env var) and adapter kwargs."
        ),
    )
    integration_sinks: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_INTEGRATION_SINKS),
        description=(
            "Integration slug -> NotificationSink class path used for rules that deliver "
            "through an owner's messaging integration. A connection whose slug is not "
            "listed may store a NotificationSink class path as its adapter."
        ),
    )
    public_web_url: str = Field(
        default="",
        description=(
            "Browser-facing Forge web origin used for deep links in delivered messages. "
            "Empty uses the host's public origin."
        ),
    )
    host_label: str = Field(
        default="",
        description="Label for this Forge host in delivered messages; empty uses its hostname.",
    )
    session_link_path: str = Field(
        default="/volundr/sessions/{session_id}#notification-{notification_id}",
        description="Deep-link path for a session notification ({session_id}, {notification_id}).",
    )
    feed_link_path: str = Field(
        default="/volundr/notifications",
        description="Deep-link path for a notification without a session.",
    )
    resident_link_path: str = Field(
        default="/ravn?ravn={resident_id}&tab=chat",
        description=(
            "Deep-link path for a resident's notification from its main room "
            "({resident_id}, {notification_id})."
        ),
    )
    resident_session_link_path: str = Field(
        default="/ravn?ravn={resident_id}&tab=chat&session={resident_session_id}",
        description=(
            "Deep-link path for a resident's notification from one of its channels "
            "({resident_id}, {resident_session_id}, {notification_id})."
        ),
    )

    @model_validator(mode="after")
    def _valid(self) -> "NotificationsConfig":
        if self.default_page_size > self.max_page_size:
            raise ValueError("notifications.default_page_size must not exceed max_page_size")
        names: set[str] = set()
        for sink in self.sinks:
            name, adapter = sink.get("name"), sink.get("adapter")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Every notifications.sinks entry needs a non-empty 'name'")
            if not isinstance(adapter, str) or "." not in adapter:
                raise ValueError(f"notifications.sinks[{name!r}] needs an 'adapter' class path")
            if len(name) > MAX_SINK_NAME_CHARS or not re.fullmatch(SINK_NAME_PATTERN, name):
                raise ValueError(
                    f"notifications sink name {name!r} must match {SINK_NAME_PATTERN} "
                    f"(at most {MAX_SINK_NAME_CHARS} characters)"
                )
            if name == INTEGRATION_SINK.name:
                raise ValueError("'integration' is reserved for messaging-integration rules")
            if name in names:
                raise ValueError(f"Duplicate notifications sink name {name!r}")
            secret_env = sink.get("secret_kwargs_env", {})
            if not isinstance(secret_env, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in secret_env.items()
            ):
                raise ValueError(
                    f"notifications.sinks[{name!r}].secret_kwargs_env must map kwarg names "
                    "to environment variable names"
                )
            names.add(name)
        for slug, adapter in self.integration_sinks.items():
            if not slug or "." not in adapter:
                raise ValueError(
                    f"notifications.integration_sinks[{slug!r}] needs a sink class path"
                )
        try:
            self.session_link_path.format(session_id="s", notification_id="n")
            self.feed_link_path.format()
            self.resident_link_path.format(resident_id="r", notification_id="n")
            self.resident_session_link_path.format(
                resident_id="r", resident_session_id="c", notification_id="n"
            )
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(
                "notifications link paths may only use {session_id}, {resident_id}, "
                "{resident_session_id} (where they apply) and {notification_id}"
            ) from exc
        return self


class ForgeMcpSessionTokenConfig(BaseModel):
    """The scoped ``forge_session`` credential Forge mints for each session launch.

    The token is signed by the workload-identity issuer when that is enabled with a
    configured key (``workload_identity.signing_key_pem`` / ``signing_key_env``).
    Otherwise, as in local mini mode, Forge keeps its own RSA key in
    ``signing_key_file``, generated once with mode 0600 and never overwritten.

    Its lifetime is tied to the session launch: every start mints a new token and
    revokes the previous one, and Forge checks each presented token against the live
    session row, so ``ttl_seconds`` is only a backstop.
    """

    enabled: bool = Field(
        default=True,
        description="Mint a scoped session credential at launch (false: brokers keep "
        "their own credential and the Forge MCP offers no grants).",
    )
    ttl_seconds: int = Field(
        default=30 * 24 * 3600,
        ge=300,
        description="Backstop lifetime of a session credential; a restart re-mints it.",
    )
    signing_key_file: str = Field(
        default="~/.niuu/forge-session-signing-key.pem",
        description="Private RSA key used when workload identity has no configured key.",
    )
    signing_key_bits: int = Field(
        default=2048, ge=2048, description="Size of a generated signing key."
    )
    issuer: str = Field(
        default="niuu-forge-session",
        min_length=1,
        description="Issuer of tokens signed with signing_key_file.",
    )
    key_id: str = Field(
        default="niuu-forge-session", min_length=1, description="kid of signing_key_file."
    )
    audiences: list[str] = Field(
        default_factory=lambda: ["volundr-api"],
        min_length=1,
        description="Audiences of tokens signed with signing_key_file.",
    )
    projection_adapter: str = Field(
        default="",
        description=(
            "Delivers the credential to brokers whose pod manager cannot hand it over "
            "itself (Flux and Kubernetes session pods): an ExecutionCredentialProjectionPort "
            "class path, e.g. volundr.adapters.outbound.execution_credential_k8s."
            "KubernetesExecutionCredentialProjection, which writes one Secret per "
            "session and mounts it into the session pod. Empty: only pod managers that "
            "deliver it themselves (local processes) give brokers a credential."
        ),
    )
    projection_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Constructor kwargs of projection_adapter (namespace, mount_path, ...).",
    )
    projection_secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of projection kwarg names to env vars holding secret values.",
    )


class ForgeMcpMeshIdentityConfig(BaseModel):
    """The mesh certificate Forge issues to a session's peer (``POST .../mesh-identity``).

    A certificate proves to other mesh peers who sent a message (``niuu.mesh.attestation``).
    It is signed with the same key as the ``forge_session`` credentials, so it exists only
    where ``forge_mcp.session_tokens`` is enabled; with that enabled, the mesh identity
    must come up too, and Forge refuses to start when it cannot. Whether the mesh proves
    its senders at all is the one decision ``mesh.attestation.required``: with it false
    Forge issues no certificates, signs nothing and publishes no revocations. There is no
    separate switch here, so the two cannot disagree.
    """

    model_config = ConfigDict(extra="forbid")

    ttl_seconds: int = Field(
        default=900,
        ge=2,
        description="Lifetime of a mesh certificate. It bounds how long a leaked or stale "
        "certificate is believed by other peers (a stopped session is also revoked at once "
        "when Forge has an event bus; without one this is the only bound). Peers renew "
        "in the background at half of it, so a shorter value means more renewal calls to "
        "Forge and a longer one a longer exposure.",
    )


class ForgeMcpHttpConfig(BaseModel):
    """The Forge-hosted MCP endpoint (``POST /api/v1/forge/mcp``) for external agents."""

    enabled: bool = Field(default=True, description="Serve the HTTP MCP endpoint.")
    allowed_origins: list[str] = Field(
        default_factory=list,
        description=(
            "Browser origins allowed to call the endpoint (exact scheme://host[:port]). "
            "Requests without an Origin header (agents, CLIs) are always allowed; any "
            "other Origin is refused with 403 to stop DNS-rebinding and CSRF."
        ),
    )
    list_default_limit: int = Field(default=20, ge=1, description="Default list size.")
    list_max_limit: int = Field(default=50, ge=1, description="Upper bound for list tools.")
    transcript_default_turns: int = Field(default=10, ge=1, description="Default turns.")
    transcript_max_turns: int = Field(default=30, ge=1, description="Upper bound on turns.")
    transcript_turn_max_chars: int = Field(default=2000, ge=1, description="Chars per turn.")
    output_max_chars: int = Field(default=24000, ge=1024, description="Tool result bound.")
    request_timeout_seconds: float = Field(
        default=20.0, gt=0, description="Timeout for one Forge REST call made by a tool."
    )
    max_body_bytes: int = Field(
        default=1024 * 1024, ge=1024, description="Largest JSON-RPC request accepted."
    )

    @model_validator(mode="after")
    def _ordered_limits(self) -> "ForgeMcpHttpConfig":
        if self.list_default_limit > self.list_max_limit:
            raise ValueError("forge_mcp.http.list_default_limit must not exceed list_max_limit")
        if self.transcript_default_turns > self.transcript_max_turns:
            raise ValueError(
                "forge_mcp.http.transcript_default_turns must not exceed transcript_max_turns"
            )
        return self


class ToolResultPreviewConfig(BaseModel):
    """Sizes of tool-result image previews (``GET …/tool-result/{id}/preview``).

    Example YAML::

        tool_result_preview:
          default_edge: 400   # served when the client sends no ?max_edge=
          min_edge: 320
          max_edge: 1600
    """

    default_edge: int = Field(
        default=PREVIEW_MAX_EDGE,
        gt=0,
        description="Longest edge (px) served without ?max_edge=; existing clients get this.",
    )
    min_edge: int = Field(
        default=PREVIEW_MIN_EDGE,
        gt=0,
        description="Smallest ?max_edge= accepted; smaller requests get 422.",
    )
    max_edge: int = Field(
        default=PREVIEW_EDGE_LIMIT,
        gt=0,
        description="Largest ?max_edge= accepted (bounds Pillow work and cache bytes per "
        "preview); larger requests get 422 and should fetch the full image instead.",
    )

    @model_validator(mode="after")
    def _ordered(self) -> "ToolResultPreviewConfig":
        if not self.min_edge <= self.default_edge <= self.max_edge:
            raise ValueError(
                "tool_result_preview needs min_edge <= default_edge <= max_edge "
                f"(got {self.min_edge}, {self.default_edge}, {self.max_edge})"
            )
        return self


class ForgeMcpConfig(BaseModel):
    """Forge MCP credentials and grants.

    Example YAML::

        forge_mcp:
          default_grants: []          # message, lifecycle: added to every session
          role_grants:                # by project role (coordination.role)
            coordinator: [lifecycle, message]
            worker: [message]
          session_tokens:
            enabled: true
            ttl_seconds: 2592000
            signing_key_file: ~/.niuu/forge-session-signing-key.pem
          mesh_identity:
            enabled: true
            ttl_seconds: 900
          http:
            enabled: true
            allowed_origins: []
    """

    default_grants: list[ForgeMcpGrant] = Field(
        default_factory=list,
        description="Grants every session's credential carries (on top of the launch "
        "spec's and the session-create request's).",
    )
    role_grants: dict[str, list[ForgeMcpGrant]] = Field(
        default_factory=lambda: {
            "coordinator": [ForgeMcpGrant.LIFECYCLE, ForgeMcpGrant.MESSAGE],
            "worker": [ForgeMcpGrant.MESSAGE],
        },
        description="Grants a session's credential carries by its project role "
        "(coordination.role), applied at every mint. A worker may message any session "
        "its owner owns (to hand work over, ask or answer); it cannot create, start or "
        "stop sessions. A role not listed gets none; set {} to grant nothing by role.",
    )
    session_tokens: ForgeMcpSessionTokenConfig = Field(default_factory=ForgeMcpSessionTokenConfig)
    mesh_identity: ForgeMcpMeshIdentityConfig = Field(default_factory=ForgeMcpMeshIdentityConfig)
    http: ForgeMcpHttpConfig = Field(default_factory=ForgeMcpHttpConfig)


#: Forge's default identity: every caller is admitted (mini mode).
ALLOW_ALL_IDENTITY_ADAPTER = "volundr.adapters.outbound.identity.AllowAllIdentityAdapter"
#: Every class path of that adapter (``volundr`` re-exports ``identity``'s).
ALLOW_ALL_IDENTITY_ADAPTERS = frozenset(
    {ALLOW_ALL_IDENTITY_ADAPTER, "identity.adapters.identity.AllowAllIdentityAdapter"}
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

    adapter: str = Field(default=ALLOW_ALL_IDENTITY_ADAPTER)
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


class WorkflowExecutionCredentialsConfig(BaseModel):
    """Rotation of scoped coordinator credentials for developer workflows."""

    enabled: bool = Field(
        default=False,
        description="Project and rotate exact-scope credentials for developer coordinators.",
    )
    projection_adapter: str = Field(
        default="",
        description="Fully-qualified ExecutionCredentialProjectionPort adapter class.",
    )
    projection_kwargs: dict[str, Any] = Field(default_factory=dict)
    projection_secret_kwargs_env: dict[str, str] = Field(default_factory=dict)
    refresh_interval_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Seconds between active-session token replacement cycles.",
    )
    admission_roles: tuple[str, ...] = Field(
        default=("volundr:developer",),
        description=(
            "Gateway admission roles on the scoped JWT; route scope still limits authority."
        ),
    )

    @model_validator(mode="after")
    def _configured_adapter(self) -> "WorkflowExecutionCredentialsConfig":
        if self.enabled and not self.projection_adapter.strip():
            raise ValueError(
                "workflow_execution_credentials.projection_adapter is required when enabled"
            )
        return self


class OAuthSpecConfig(BaseModel):
    """OAuth2 provider specification in config."""

    authorize_url: str
    token_url: str
    revoke_url: str = ""
    scopes: list[str] = Field(default_factory=list)
    token_field_mapping: dict[str, str] = Field(default_factory=dict)
    extra_authorize_params: dict[str, str] = Field(default_factory=dict)
    extra_token_params: dict[str, str] = Field(default_factory=dict)
    token_request_format: Literal["form", "json"] = Field(
        default="form",
        description="Encoding used by the provider's token endpoint.",
    )
    client_secret_required: bool = Field(
        default=False,
        description="Whether every OAuth application must provide a client secret.",
    )
    device_authorization_url: str = Field(
        default="",
        description="RFC 8628 device authorization endpoint; enables sign-in without a callback.",
    )


class OAuthClientConfig(BaseModel):
    """Client credentials for a single OAuth integration.

    The device flow only needs the (public) client id; the secret is for the
    authorization-code flow behind a callback URL.
    """

    client_id: str
    client_secret: str = ""
    base_url: str = ""


class OAuthConfig(BaseModel):
    """Top-level OAuth configuration."""

    mini_mode_refresh_enabled: bool = Field(
        default=True, description="Run the legacy OAuth refresher only in mini-mode."
    )
    redirect_base_url: str = ""
    mcp_request_timeout_seconds: float = Field(default=15.0, gt=0)
    mcp_state_ttl_seconds: int = Field(default=600, gt=0)
    mcp_internal_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "Hostnames of your own MCP servers and their OAuth issuers that may resolve "
            "to private addresses: exact names or '*.domain' suffixes, e.g. "
            "'*.asgard.niuu.world'. Every other MCP host must resolve publicly."
        ),
    )
    clients: dict[str, OAuthClientConfig] = Field(default_factory=dict)

    @field_validator("mcp_internal_hosts")
    @classmethod
    def _normalize_mcp_internal_hosts(cls, patterns: list[str]) -> list[str]:
        return [normalize_internal_host_pattern(pattern) for pattern in patterns]


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
    env_from_config: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Session environment variables taken from the connection's (non-secret) config, "
            "env var name → config key. A missing key fails the launch."
        ),
    )
    auth_type: str = "api_key"
    oauth: OAuthSpecConfig | None = None
    file_mounts: dict[str, str] = Field(default_factory=dict)
    credential_enrollment: dict[str, str] | None = None
    model_vendor: str = Field(
        default="",
        description=(
            "Model vendor an AI provider connection unlocks (anthropic, openai, xai, "
            "deepseek). Session definitions name the vendors they accept in "
            "compatible_providers, so this is what decides which engines a connected "
            "account makes launchable."
        ),
    )
    key_probe: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Cheap authenticated request that proves an API key works: url, auth "
            "('bearer' or a header name) and optional extra headers."
        ),
    )
    team_install: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Lets a project owner install this integration for the project's team, acting "
            "as a bot or service account: {method: oauth, authorize_params, scopes} for the "
            "provider's app install, or {method: client_credentials, token_url, "
            "token_params} for a service account. Absent: personal connections only."
        ),
    )


GITHUB_DEVICE_AUTHORIZATION_URL = "https://github.com/login/device/code"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITLAB_DEVICE_AUTHORIZATION_URL = "https://gitlab.com/oauth/authorize_device"
GITLAB_TOKEN_URL = "https://gitlab.com/oauth/token"


# The seeded "Model server" provider (see cli.commands.platform) and the env
# vars that tell a session's Skuld to route Claude Code and Codex through the
# gateway. MODEL_GATEWAY_TOKEN_ENV is imported from the contributor that owns
# it (volundr.adapters.outbound.contributors.model_gateway) rather than
# duplicated as a literal string here. The IntegrationContributor's
# env_from_config path below (not ModelGatewayContributor, which isn't wired
# in docker/mini mode) is what actually emits both env vars for a seeded
# "model-server" connection — see model_server_seed_connections() in
# cli.commands.platform, which supplies the "gateway_url" and "token" config
# keys these map to. A connection missing either key fails loudly at session
# creation (IntegrationContributor.contribute) rather than spawning a session
# that can't reach the gateway.
MODEL_SERVER_SLUG = "model-server"
MODEL_GATEWAY_URL_ENV = "SKULD__MODEL_GATEWAY__URL"


def _default_integration_definitions() -> list[IntegrationDefinitionConfig]:
    """Return the built-in integration catalog entries."""
    return [
        IntegrationDefinitionConfig(
            slug="mcp",
            name="MCP server",
            description="Connect an MCP server using OAuth or an API token",
            integration_type="mcp",
            credential_schema={
                "required": ["access_token"],
                "properties": {"access_token": {"label": "API token", "type": "password"}},
            },
            config_schema={
                "required": ["mcp_url"],
                "properties": {
                    "mcp_url": {"label": "MCP server URL", "type": "string"},
                    "name": {"label": "Display name", "type": "string"},
                },
            },
        ),
        IntegrationDefinitionConfig(
            slug="github",
            name="GitHub",
            description="GitHub source control — repo browsing, clone, PRs, and gh CLI",
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
            # gh in the session image signs in with GH_TOKEN.
            env_from_credentials={"GH_TOKEN": "token"},
            # Sign in with GitHub: device flow of an OAuth App the person owns
            # (client id registered from the wizard or under oauth.clients.github;
            # no secret, no callback). Without scopes GitHub hands out a token
            # that only reads public data, so sessions could neither see private
            # or organisation repositories nor push: `repo` covers code and pull
            # requests everywhere the account can reach, `read:org` the
            # organisation membership, `workflow` files under .github/workflows.
            oauth=OAuthSpecConfig(
                authorize_url="https://github.com/login/oauth/authorize",
                token_url=GITHUB_TOKEN_URL,
                device_authorization_url=GITHUB_DEVICE_AUTHORIZATION_URL,
                scopes=["repo", "read:org", "workflow"],
            ),
            credential_enrollment={
                "method": "oauth_device",
                "credential_field": "token",
                "default_credential_name": "github-signin",
            },
        ),
        IntegrationDefinitionConfig(
            slug="gitlab",
            name="GitLab",
            description="GitLab source control — repo browsing, clone, MRs, and glab CLI",
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
            # glab in the session image signs in with GITLAB_TOKEN.
            env_from_credentials={"GITLAB_TOKEN": "token"},
            # Sign in with GitLab (17.2+): device grant of an application whose
            # public client id is configured under oauth.clients.gitlab.
            oauth=OAuthSpecConfig(
                authorize_url="https://gitlab.com/oauth/authorize",
                token_url=GITLAB_TOKEN_URL,
                device_authorization_url=GITLAB_DEVICE_AUTHORIZATION_URL,
                scopes=["api"],
            ),
            credential_enrollment={
                "method": "oauth_device",
                "credential_field": "token",
                "default_credential_name": "gitlab-signin",
            },
        ),
        IntegrationDefinitionConfig(
            slug="jira",
            name="Jira Cloud",
            description="Jira Cloud issue tracking — search, issue browsing, and status updates",
            integration_type="issue_tracker",
            adapter="volundr.adapters.outbound.jira.JiraAdapter",
            icon="jira",
            credential_schema={
                "required": ["email", "api_token"],
                "properties": {
                    "email": {"label": "Atlassian account email", "type": "string"},
                    "api_token": {"label": "API token", "type": "password"},
                },
            },
            config_schema={
                "required": ["site_url"],
                "properties": {
                    "site_url": {
                        "label": "Jira site URL",
                        "type": "url",
                    },
                    "cloud_id": {
                        "label": "Cloud ID (scoped API tokens only)",
                        "type": "string",
                    },
                    "project_keys": {
                        "label": "Allowed project keys",
                        "type": "string[]",
                        "description": (
                            "Optional. Only expose issues from these Jira projects, "
                            "for example NIUU, PLATFORM."
                        ),
                    },
                    "labels": {
                        "label": "Allowed issue labels",
                        "type": "string[]",
                        "description": (
                            "Optional. Only expose issues carrying at least one of these labels."
                        ),
                    },
                    "issue_type": {
                        "label": "Issue type for new work",
                        "type": "string",
                        "description": (
                            "Issue type used when Ting or the tracker API creates Jira "
                            "work. Defaults to Task."
                        ),
                    },
                },
            },
            auth_type="api_key",
            oauth=OAuthSpecConfig(
                authorize_url="https://auth.atlassian.com/authorize",
                token_url="https://auth.atlassian.com/oauth/token",
                scopes=[
                    "read:jira-work",
                    "write:jira-work",
                    "read:jira-user",
                    "offline_access",
                ],
                extra_authorize_params={
                    "audience": "api.atlassian.com",
                    "prompt": "consent",
                },
                token_request_format="json",
                client_secret_required=True,
            ),
            credential_enrollment={
                "method": "oauth_authorization_code",
                "credential_field": "access_token",
                "default_credential_name": "jira-signin",
            },
            # A team's Jira is an Atlassian service account's OAuth 2.0 client
            # credentials, minted into short-lived tokens; it needs cloud_id.
            team_install={
                "method": "client_credentials",
                "token_url": "https://auth.atlassian.com/oauth/token",
                "token_params": {"audience": "api.atlassian.com"},
                "required_config": ["cloud_id"],
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
                "transport": "http",
                "url": "https://mcp.linear.app/mcp",
                "token_field": "api_key",
            },
            auth_type="api_key",
            oauth=OAuthSpecConfig(
                authorize_url="https://linear.app/oauth/authorize",
                token_url="https://api.linear.app/oauth/token",
                # Linear takes a comma-separated scope list.
                extra_authorize_params={"scope": "read,write,issues:create,comments:create"},
                client_secret_required=True,
            ),
            # A team's Linear is the OAuth app acting as itself, never as the installer.
            team_install={"method": "oauth", "authorize_params": {"actor": "app"}},
        ),
        IntegrationDefinitionConfig(
            slug="anthropic",
            name="Anthropic (Claude API)",
            description="Anthropic API key for Claude models",
            integration_type="ai_provider",
            model_vendor="anthropic",
            icon="anthropic",
            credential_schema={
                "required": ["api_key"],
                "properties": {"api_key": {"label": "API Key", "type": "password"}},
            },
            env_from_credentials={"ANTHROPIC_API_KEY": "api_key"},
            key_probe={
                "url": "https://api.anthropic.com/v1/models",
                "auth": "x-api-key",
                "headers": {"anthropic-version": "2023-06-01"},
            },
        ),
        IntegrationDefinitionConfig(
            slug="openai",
            name="OpenAI",
            description="OpenAI API key for GPT/Codex models",
            integration_type="ai_provider",
            model_vendor="openai",
            icon="openai",
            credential_schema={
                "required": ["api_key"],
                "properties": {"api_key": {"label": "API Key", "type": "password"}},
            },
            env_from_credentials={"OPENAI_API_KEY": "api_key"},
            key_probe={"url": "https://api.openai.com/v1/models", "auth": "bearer"},
        ),
        IntegrationDefinitionConfig(
            slug="xai",
            name="xAI (Grok)",
            description="xAI API key for Grok models",
            integration_type="ai_provider",
            model_vendor="xai",
            icon="xai",
            credential_schema={
                "required": ["api_key"],
                "properties": {"api_key": {"label": "API Key", "type": "password"}},
            },
            env_from_credentials={"XAI_API_KEY": "api_key"},
            key_probe={"url": "https://api.x.ai/v1/models", "auth": "bearer"},
        ),
        IntegrationDefinitionConfig(
            slug="meta",
            name="Meta (Muse)",
            description="Meta API key for Muse Code sessions",
            integration_type="ai_provider",
            model_vendor="meta",
            icon="meta",
            credential_schema={
                "required": ["api_key"],
                "properties": {"api_key": {"label": "API Key", "type": "password"}},
            },
            env_from_credentials={"META_API_KEY": "api_key"},
        ),
        IntegrationDefinitionConfig(
            slug="grok-build",
            name="Grok Build (xAI sign-in)",
            description="Sign in with your SuperGrok or X Premium+ account for Grok Build sessions",
            integration_type="ai_provider",
            model_vendor="xai",
            icon="xai",
            credential_schema={},
            auth_type="device_code",
            credential_enrollment={
                "method": "grok_device",
                "credential_field": "auth.json",
                "default_credential_name": "grok-credentials",
            },
            # The grok CLI reads its session from ~/.grok/auth.json.
            file_mounts={"/home/skuld/.grok/auth.json": "auth.json"},
        ),
        IntegrationDefinitionConfig(
            slug="deepseek",
            name="DeepSeek",
            description="DeepSeek API key for DeepSeek models and the DeepSeek Harness runtime",
            integration_type="ai_provider",
            model_vendor="deepseek",
            icon="deepseek",
            credential_schema={
                "required": ["api_key"],
                "properties": {"api_key": {"label": "API Key", "type": "password"}},
            },
            env_from_credentials={"DEEPSEEK_API_KEY": "api_key"},
            key_probe={"url": "https://api.deepseek.com/models", "auth": "bearer"},
        ),
        IntegrationDefinitionConfig(
            slug="claude-code",
            name="Claude Code (subscription)",
            description="Connect your Claude subscription for Claude Code sessions",
            integration_type="ai_provider",
            model_vendor="anthropic",
            icon="anthropic",
            credential_schema={},
            auth_type="browser_login",
            credential_enrollment={
                "method": "claude_setup",
                "credential_field": "token",
                "default_credential_name": "claude-code-credentials",
            },
            env_from_credentials={"CLAUDE_CODE_OAUTH_TOKEN": "token"},
        ),
        IntegrationDefinitionConfig(
            slug="codex",
            name="OpenAI Codex (ChatGPT)",
            description="User-scoped ChatGPT subscription login for Codex runtimes",
            integration_type="ai_provider",
            model_vendor="openai",
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
            slug=MODEL_SERVER_SLUG,
            name="Model server",
            description=(
                "A model you serve yourself (vLLM, sparkrun, Ollama, anything "
                "OpenAI-compatible), reached through the platform's model gateway. "
                "Registered from Settings → Runtime → Model server, not added here."
            ),
            integration_type="ai_provider",
            model_vendor="local",
            icon="server",
            auth_type="none",
            credential_schema={},
            config_schema={
                "properties": {
                    "provider": {"label": "Gateway provider", "type": "string"},
                    "gateway_url": {"label": "Gateway URL", "type": "string"},
                    "token": {"label": "Gateway token", "type": "string"},
                    "models": {"label": "Models", "type": "list"},
                },
            },
            env_from_config={
                MODEL_GATEWAY_URL_ENV: "gateway_url",
                MODEL_GATEWAY_TOKEN_ENV: "token",
            },
        ),
        IntegrationDefinitionConfig(
            slug="telegram",
            name="Telegram",
            description="Telegram bot — notifications, session alerts, and dispatch commands",
            integration_type="messaging",
            # Ting's per-user channel; Forge delivery picks its sink by slug
            # (notifications.integration_sinks), whatever the stored adapter.
            adapter="niuu.adapters.notifications.telegram.TelegramNotificationAdapter",
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
        IntegrationDefinitionConfig(
            slug="slack",
            name="Slack",
            description=(
                "Slack bot for a project team: posts to a team channel and messages a "
                "person directly when something waits on them"
            ),
            integration_type="messaging",
            # Forge delivers through the slug's sink (notifications.integration_sinks).
            adapter="",
            icon="slack",
            credential_schema={
                "required": ["access_token"],
                "properties": {"access_token": {"label": "Bot token", "type": "password"}},
            },
            config_schema={
                "properties": {
                    "channel": {
                        "label": "Team channel",
                        "type": "string",
                        "description": (
                            "Where team posts go, for example #payments-team. Invite the "
                            "bot to the channel first."
                        ),
                    },
                },
            },
            auth_type="oauth2_authorization_code",
            oauth=OAuthSpecConfig(
                authorize_url="https://slack.com/oauth/v2/authorize",
                token_url="https://slack.com/api/oauth.v2.access",
                # Slack takes a comma-separated bot scope list.
                extra_authorize_params={
                    "scope": "chat:write,im:write,users:read,users:read.email",
                },
                client_secret_required=True,
            ),
            team_install={"method": "oauth", "team_only": True},
        ),
    ]


class SessionRoomConfig(BaseModel):
    """How the platform reaches a session's broker for room and route calls."""

    internal_base_url: str = Field(
        default="",
        description=(
            "Origin to dial instead of the session's public chat endpoint origin, "
            "e.g. http://127.0.0.1:8080 when the public address is not reachable "
            "from the platform process itself (single-host Docker). Empty = public."
        ),
    )


class IntegrationsConfig(BaseModel):
    """Integration catalog configuration."""

    repository: DynamicAdapterConfig | None = None

    database_name: str = Field(
        default="",
        description=(
            "Shared integration database on the configured PostgreSQL server; "
            "empty uses the service database."
        ),
    )
    definitions: list[IntegrationDefinitionConfig] = Field(
        default_factory=_default_integration_definitions,
    )
    definition_files: list[str] = Field(
        default_factory=list,
        description=(
            "YAML or JSON files containing additional integration definitions. "
            "Files are loaded at startup and merged with the configured catalog."
        ),
    )
    module_manifest_files: list[str] = Field(
        default_factory=list,
        description=(
            "Versioned manifests for trusted external packages. Manifest components "
            "are contract-checked at startup but remain inactive until selected by config."
        ),
    )
    allow_definition_overrides: bool = Field(
        default=False,
        description=(
            "Allow a definition loaded from a later external file to replace an "
            "existing definition with the same slug. Duplicate slugs fail startup "
            "when this is false."
        ),
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


# The seed list is declared before its item type; resolve the forward reference
# so settings sources (env, files) can parse it instead of warning.
IntegrationsConfig.model_rebuild()


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

    service_adapter: str = "niuu.domain.services.pat.PATService"
    service_kwargs: dict = Field(default_factory=dict)
    validator_adapter: str = "niuu.domain.services.pat_validator.PATValidator"
    validator_kwargs: dict = Field(default_factory=dict)
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
    websocket_check_interval: float = Field(
        default=30.0,
        gt=0,
        allow_inf_nan=False,
        description="Seconds between revocation checks on open WebSockets; expiry is immediate.",
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
    provenance: ProvenanceConfig = Field(default_factory=ProvenanceConfig)
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
    catalog_retry_initial_seconds: float = Field(
        default=0.1,
        gt=0,
        description=(
            "First delay before retrying a failed Bifrost catalog load. Every failure is "
            "retried (doubling up to catalog_retry_max_seconds) and logged; the last good "
            "catalog stays in use meanwhile."
        ),
    )
    catalog_retry_max_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Longest delay between retries of a failed Bifrost catalog load.",
    )
    session_gateway_url: str = Field(
        default="",
        description=(
            "Bifrost URL as reachable from a session pod. When set, sessions on a "
            "model the catalog marks provider=local get SKULD__MODEL_GATEWAY__URL "
            "pointing here. Empty disables it."
        ),
    )
    auth: HttpAuthAdapterConfig = Field(default_factory=HttpAuthAdapterConfig)


class ObservatoryGuildConfig(BaseModel):
    """Guild dependency config consumed by the host-mounted Observatory app."""

    url: str = Field(
        default="http://localhost:8080",
        description="Base URL for the mounted Guild/niuu API host.",
    )


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
        default=20.0,
        gt=0,
        description=(
            "Timeout for each Guild-to-Observatory request. An Observatory "
            "builds its fragment on demand, so the richest source is the "
            "slowest: ymir runs five adapters including outbound calls to "
            "Bifrost, Ravn and Ting and answers in ~8.5s. At the previous 5s "
            "it timed out intermittently, and httpx.ReadTimeout carries no "
            "message, so the failure reported as an empty string."
        ),
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


class ObservatoryFragmentInboxConfig(BaseModel):
    """Push inbox for sources the aggregator cannot reach."""

    ttl_seconds: float = Field(
        default=180.0,
        gt=0,
        description=(
            "How long a pushed fragment stays fresh. A source that has not "
            "published within this window is reported as stale rather than "
            "dropped, so a host that stopped reporting stays visible."
        ),
    )


class ObservatoryConfig(BaseModel):
    """Observatory plugin configuration."""

    guild: ObservatoryGuildConfig = Field(default_factory=ObservatoryGuildConfig)
    discovery: list[DynamicAdapterConfig] = Field(default_factory=list)
    directory: AgentDirectoryConfig = Field(default_factory=AgentDirectoryConfig)
    fragments: ObservatoryFragmentInboxConfig = Field(
        default_factory=ObservatoryFragmentInboxConfig
    )
    platform_tenant_id: str = Field(
        default=DEFAULT_PLATFORM_TENANT_ID,
        description=(
            "The org that runs this deployment. It owns anything that names no org, "
            "so clusters, hosts and services are shown to it alone."
        ),
    )


class SessionUsageConfig(BaseModel):
    """Model metadata for status when the native stream omits its raw window.

    Explicit runtime windows take precedence. Unknown models remain unknown;
    these defaults are the LEX-1421 model contract, not a token heuristic.
    """

    model_windows: dict[str, Annotated[int, Field(strict=True, gt=0, le=2**53 - 1)]] = Field(
        default_factory=lambda: {
            "claude-opus-5-5": 1_000_000,
            "claude-fable-5-1": 1_000_000,
            "claude-sonnet-5-5": 1_000_000,
        },
        description="Positive context windows representable exactly by JSON number consumers",
    )


class HostIdentityConfig(BaseModel):
    """How this Forge names itself to the control plane (F7, ``GET /instance``).

    ``host`` is the control plane's host-map key (``thor``, ``mini-m6``, ``thor-exp``);
    empty falls back to ``projects.instance_id``, then ``local``. ``tailnet_name`` is
    the phone-reachable name (``thor.tail737f2a.ts.net``); empty falls back to
    ``server_public_host``.
    """

    host: str = Field(default="", max_length=100, pattern=r"^[a-zA-Z0-9_.-]*$")
    tailnet_name: str = Field(default="", max_length=253)


def _default_artifact_type_urls() -> dict[str, str]:
    # The claude.ai Artifact types seen in the capture corpus (F1t); a new type also
    # classifies by its instructions' front-matter name (``artifacts.type_names``).
    return {
        "https://claude.ai/artifact/8jTsAFQMFDb2oA8MsPJ2eL": "deck",
        "https://claude.ai/artifact/QKN21svewxgyPb6SYRqWnd": "design",
        "https://claude.ai/artifact/Rp9naXUCj2xozpUkyQy19W": "doc",
        "https://claude.ai/artifact/5M7UeXXcx16TP3vzVFNDzd": "design_system",
    }


def _default_artifact_type_names() -> dict[str, str]:
    return {
        "slides": "deck",
        "design": "design",
        "pages": "doc",
        "docs": "doc",
        "design-system": "design_system",
        "design_system": "design_system",
    }


class ArtifactsConfig(BaseModel):
    """Artifact publications (F1) and their burst notices (F1n): model/bursts.md."""

    enabled: bool = Field(default=True, description="Record publications and serve their read.")
    notices_enabled: bool = Field(
        default=True,
        description="Announce each live burst with one notice after the hold (F1n).",
    )
    burst_window_seconds: float = Field(
        default=1800.0, gt=0, description="A burst's sliding window on each side."
    )
    hold_seconds: float = Field(
        default=120.0, ge=0, description="Durable hold before a live burst's notice."
    )
    live_lag_seconds: float = Field(
        default=600.0,
        ge=0,
        description="A row ingested within this lag of its timestamp is live; else backfill.",
    )
    tick_interval_seconds: float = Field(
        default=5.0, gt=0, description="How often the hold looks for due bursts."
    )
    emit_batch_size: int = Field(default=100, ge=1)
    max_emit_failures: int = Field(
        default=5, ge=1, description="Failed emissions before a burst is settled silently."
    )
    backfill_page_size: int = Field(default=500, ge=1)
    default_page_size: int = Field(default=200, ge=1)
    max_page_size: int = Field(default=1000, ge=1)
    backfill_on_start: bool = Field(
        default=False,
        description="Replay the durable log into publications at start (always silent).",
    )
    backfill_since_days: float | None = Field(
        default=None, gt=0, description="Bound the start-up backfill to recent rows."
    )
    type_urls: dict[str, str] = Field(default_factory=_default_artifact_type_urls)
    type_names: dict[str, str] = Field(default_factory=_default_artifact_type_names)


class MemoryConfig(BaseModel):
    """How sessions reach the memory gateway (the Mimir service) for their memory server."""

    gateway_url: str = Field(
        default="",
        description=(
            "Base URL of the memory gateway as reachable from a session, e.g. "
            "http://127.0.0.1:8080/api/v1/mimir. When set, every session gets Skuld "
            "memory.enabled: true and memory.gateway_url, and Forge requires session "
            "credentials (forge_mcp.session_tokens) so the broker can authenticate to the "
            "gateway. Empty runs sessions without a memory server."
        ),
    )

    @field_validator("gateway_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        url = value.strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError(
                f"memory.gateway_url must be an http(s) URL such as "
                f"http://127.0.0.1:8080/api/v1/mimir, got {value!r}; leave it empty to run "
                "sessions without a memory server"
            )
        return url.rstrip("/")


class ProjectMemoryConfig(BaseModel):
    """The memory a project owns: where its mounts are stored and looked up."""

    repository_adapter: str = (
        "volundr.adapters.outbound.postgres_project_memory.PostgresProjectMemoryRepository"
    )
    repository_kwargs: dict[str, Any] = Field(
        default_factory=dict, description="Extra constructor kwargs for the mount store."
    )
    directory_adapter: str = (
        "volundr.adapters.outbound.mimir_registry_directory.MimirRegistryFileDirectory"
    )
    directory_kwargs: dict[str, Any] = Field(
        default_factory=lambda: {"registry_path": "~/.ravn/mimir/.mimir-registry.json"},
        description=(
            "Constructor kwargs for the registry lookup a mount is resolved with at add time "
            "and at every launch. The default reads the Mimir registry file; a missing file "
            "fails the launch of any session in a project that has mounts."
        ),
    )


class ProjectsConfig(BaseModel):
    """Storage adapters and bounded checkpoint limits; workflows live in agent skills."""

    enabled: bool = True
    instance_id: str = ""
    repository_adapter: str = (
        "volundr.adapters.outbound.postgres_projects.PostgresProjectRepository"
    )
    repository_kwargs: dict[str, Any] = Field(default_factory=dict)
    member_repository_adapter: str = (
        "volundr.adapters.outbound.postgres_project_members.PostgresProjectMemberRepository"
    )
    member_repository_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra constructor kwargs for the project member store.",
    )
    workspace_adapter: str = "volundr.adapters.outbound.project_workspace.GitProjectWorkspace"
    workspace_kwargs: dict[str, Any] = Field(default_factory=dict)
    context_bytes: int = Field(default=8192, ge=1024, le=65536)
    git_timeout_seconds: float = Field(default=15.0, gt=0)
    dispatch_wait_seconds: float = Field(default=30.0, gt=0)
    dispatch_poll_seconds: float = Field(default=0.05, gt=0)
    memory: ProjectMemoryConfig = Field(default_factory=ProjectMemoryConfig)
    team_usage_page_size: int = Field(
        default=50,
        ge=1,
        description=(
            "Uses of a project's team integrations returned per page; older uses are "
            "paged with `before`, never dropped."
        ),
    )


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
    observability: VolundrObservabilityConfig = Field(default_factory=VolundrObservabilityConfig)
    compute: ComputeConfig | None = None

    projects: ProjectsConfig = Field(default_factory=ProjectsConfig)
    runtime_health_timeout_seconds: float = Field(default=3.0, gt=0)
    forge_stream_remote_timeout_seconds: float = Field(
        default=45.0,
        gt=0,
        description="Total httpx timeout for reading a remote Guild member's session SSE stream.",
    )
    forge_stream_remote_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Connect timeout for opening a remote Guild member's session SSE stream.",
    )
    forge_stream_retry_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Delay before retrying a disconnected host in the merged session stream.",
    )
    guild_session_owner_ttl_seconds: float = Field(
        default=3600.0,
        gt=0,
        description=(
            "How long the Guild facade remembers which node owns a session (learned from "
            "Guild listings and owner lookups) and reads it there alone. A stale entry costs "
            "one 404 from the old owner, then a full search; it never hides a session."
        ),
    )
    guild_feedless_recheck_seconds: float = Field(
        default=3600.0,
        gt=0,
        description=(
            "How long the Guild's merged notification feed leaves out a node without a feed "
            "(an older Forge answering 404) before asking it again (LEX-1738)."
        ),
    )
    forge_stream_keepalive_seconds: float = Field(
        default=15.0,
        gt=0,
        description="Idle interval before the merged session stream sends a keepalive comment.",
    )
    realm_detail_stream_interval_seconds: float = Field(
        default=15.0,
        gt=0,
        description="Polling and keepalive interval for authenticated realm-detail SSE streams.",
    )
    forge_stream_queue_maxsize: int = Field(
        default=256,
        gt=0,
        description="Bound on the in-memory queue merging per-host session stream events.",
    )
    guild_transport_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "Ceiling for the connect leg of every outbound Guild call "
            "(niuu.adapters.outbound.guild_transport), and the timeout for the bare "
            "handshake that fetches a pinned instance's live certificate."
        ),
    )
    guild_transport_trusted_plaintext_host_suffixes: list[str] = Field(
        default_factory=lambda: [".svc.cluster.local", ".svc"],
        description=(
            "Host suffixes (label-boundary match, e.g. a host ending in "
            "'.svc.cluster.local') exempt from the https-unless-allow_plaintext "
            "policy in niuu.domain.transport_security, the same way localhost is: "
            "in-cluster Kubernetes service DNS never leaves the cluster's pod "
            "network, so an operator does not have to set config.allow_plaintext "
            "on every in-cluster seed. Applies at both registration/seed-time "
            "validation and outbound call-time enforcement — the same choke "
            "point Ting's Volundr calls also go through. Set to [] to require "
            "the explicit allow_plaintext opt-in everywhere, including "
            "in-cluster addresses. Never exempts config.tls_fingerprint pinning, "
            "which always requires https:// regardless of hostname."
        ),
    )
    conversation_recent_max_turns: int = Field(default=15, gt=0)
    conversation_recent_max_bytes: int = Field(default=256 * 1024, ge=4096)
    conversation_tail_initial_frames: int = Field(
        default=2000,
        gt=0,
        description=(
            "First durable event-log window, in frames, read for a `?tail=N` conversation "
            "of a session without a live broker. Widened until it holds more than N turns."
        ),
    )
    conversation_tail_growth: int = Field(
        default=2,
        ge=2,
        description="Factor by which a `?tail=N` event-log window widens per step.",
    )
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
    tool_result_preview: ToolResultPreviewConfig = Field(
        default_factory=ToolResultPreviewConfig,
        description="Tool-result image preview sizes (?max_edge= bounds and default).",
    )
    preview_cache_dir: str = Field(
        default="~/.niuu/preview-cache",
        validation_alias=AliasChoices("preview_cache_dir", "PREVIEW_CACHE_DIR"),
        description=(
            "Directory for generated tool-result image preview JPEGs (~ is "
            "expanded). Must be writable; startup fails otherwise. Kubernetes pods "
            "have a read-only root filesystem, so the chart points this at an "
            "emptyDir mount (previewCache.mountPath)."
        ),
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
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    session_usage: SessionUsageConfig = Field(default_factory=SessionUsageConfig)
    presented_files: PresentedFilesConfig = Field(default_factory=PresentedFilesConfig)
    artifacts: ArtifactsConfig = Field(default_factory=ArtifactsConfig)
    host_identity: HostIdentityConfig = Field(default_factory=HostIdentityConfig)
    forge_mcp: ForgeMcpConfig = Field(default_factory=ForgeMcpConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    mesh: MeshSettings = Field(default_factory=MeshSettings)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    authorization: AuthorizationConfig = Field(default_factory=AuthorizationConfig)
    auth_mode: str = Field(
        default="envoy",
        description=(
            "How this host trusts identity: 'envoy' (default — an Envoy sidecar "
            "verifies JWTs and forwards trusted x-auth-* headers; unchanged "
            "Kubernetes behaviour), 'none' (explicit no-auth for a host without "
            "Envoy — mini/docker mode's default), or 'oidc' (in-process JWT "
            "verification for a host without Envoy). Set by the mini/docker CLI "
            "host from auth.mode (cli.config.AuthConfig); Kubernetes deployments "
            "leave this at its default."
        ),
    )
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
    session_preview: SessionPreviewConfig = Field(
        default_factory=SessionPreviewConfig,
        description=(
            "Live preview served by this Forge (standalone deployments). In mini mode "
            "the local host serves previews (niuu host.session_preview) and only the "
            "credential settings here apply."
        ),
    )
    guild: GuildMembershipConfig = Field(
        default_factory=GuildMembershipConfig,
        description=(
            "The Guild this host belongs to and its Forge instance there, served at "
            "GET /api/v1/forge/guild and stamped on every notification."
        ),
    )
    workflow_execution_credentials: WorkflowExecutionCredentialsConfig = Field(
        default_factory=WorkflowExecutionCredentialsConfig
    )
    auth_discovery: AuthDiscoveryConfig = Field(default_factory=AuthDiscoveryConfig)
    session_room: SessionRoomConfig = Field(default_factory=SessionRoomConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    oauth: OAuthConfig = Field(default_factory=OAuthConfig)
    provisioning: ProvisioningConfig = Field(default_factory=ProvisioningConfig)
    permission_auto_approval: PermissionAutoApprovalConfig = Field(
        default_factory=PermissionAutoApprovalConfig,
        description="Server-side allow/deny policy for permission request auto approvals.",
    )
    local_git: LocalGitConfig = Field(default_factory=LocalGitConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    local_mounts: LocalMountsConfig = Field(default_factory=LocalMountsConfig)
    external_sessions: ExternalSessionsConfig = Field(default_factory=ExternalSessionsConfig)
    telegram_ingress: TelegramIngressConfig = Field(default_factory=TelegramIngressConfig)
    session_contributors: list[SessionContributorConfig] = Field(default_factory=list)
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
    def _needs_input_push_has_its_sink(self) -> "Settings":
        """``push.enabled`` delivers through the outbox; refuse it without one.

        The push gateway alerts for the needs-input moment itself, so it needs no sink.
        """
        if not self.push.enabled or self.push.gateway.enabled:
            return self
        if not self.notifications.enabled:
            raise ValueError(
                "push.enabled delivers needs-input pushes through notifications; set "
                "notifications.enabled: true, or push.enabled: false"
            )
        sink_names = {sink.get("name") for sink in self.notifications.sinks}
        if self.push.attention_sink not in sink_names:
            raise ValueError(
                f"push.enabled needs a notifications.sinks entry named "
                f"{self.push.attention_sink!r} (push.attention_sink), e.g. adapter "
                "volundr.adapters.outbound.push_notification_sink.PushNotificationSink; "
                "or set push.enabled: false"
            )
        return self

    @model_validator(mode="after")
    def _authenticated_presented_files_need_identity(self) -> "Settings":
        """``public_access: authenticated`` is a claim that the origin checks every caller.

        A Forge whose own identity adapter admits everyone cannot make it: its durable
        copies would be open to whoever reaches the URL.
        """
        presented = self.presented_files
        if presented.public_access is not PresentedFilesAccess.AUTHENTICATED:
            return self
        if not presented.public_base_url:
            return self
        if self.identity.adapter in ALLOW_ALL_IDENTITY_ADAPTERS:
            raise ValueError(
                "presented_files.public_access: authenticated needs an identity adapter "
                f"that authenticates; identity.adapter is {self.identity.adapter}. "
                "Configure the platform identity (for example EnvoyHeaderIdentityAdapter), "
                "or use public_access: tailnet, or leave public_base_url empty."
            )
        return self

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
