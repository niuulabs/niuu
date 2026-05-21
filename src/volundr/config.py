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

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from bifrost.config import BifrostConfig
from niuu.config import (
    CorsConfig,
    GitHubConfig,  # noqa: F401
    GitHubInstance,  # noqa: F401
    GitLabConfig,  # noqa: F401
    GitLabInstance,  # noqa: F401
    HttpAuthAdapterConfig,
    InstanceRegistryConfig,
)
from ravn.config import PersonaSourceConfig
from volundr.domain.models import IntegrationType, SecretType


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


class LoggingConfig(BaseModel):
    """Logging configuration.

    Reads LOG_LEVEL and LOG_FORMAT environment variables if set.
    """

    level: str = Field(default_factory=lambda: os.environ.get("LOG_LEVEL", "info"))
    format: str = Field(default_factory=lambda: os.environ.get("LOG_FORMAT", "text"))


class DatabaseConfig(BaseModel):
    """PostgreSQL database configuration."""

    host: str = Field(default="localhost")
    port: int = Field(default=5432)
    user: str = Field(default="volundr")
    password: str = Field(default="volundr")
    name: str = Field(default="volundr")
    min_pool_size: int = Field(default=5)
    max_pool_size: int = Field(default=20)

    @property
    def database(self) -> str:
        """Alias for name to maintain compatibility."""
        return self.name

    @property
    def dsn(self) -> str:
        """Return PostgreSQL connection string."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


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


class MCPServerEntry(BaseModel):
    """Configuration for an available MCP server."""

    name: str
    type: str = "stdio"
    command: str | None = None
    url: str | None = None
    args: list[str] = Field(default_factory=list)
    description: str = ""


class SessionDefinitionConfig(BaseModel):
    """Configuration for a single session definition (e.g. skuldClaude, skuldCodex).

    Session definitions describe available AI backend configurations.
    Each definition has a unique key, display metadata, and a ``defaults``
    dict that gets merged into Helm values when a session is created with
    this definition.
    """

    enabled: bool = True
    display_name: str = ""
    description: str = ""
    labels: list[str] = Field(default_factory=list)
    default_model: str = ""
    compatible_providers: list[str] = Field(
        default_factory=list,
        description=(
            "Model providers this runtime accepts (e.g. ['anthropic'], "
            "['openai']). An empty list means the runtime is provider-neutral "
            "and accepts any model."
        ),
    )
    defaults: dict[str, Any] = Field(default_factory=dict)


def _default_session_definitions() -> dict[str, SessionDefinitionConfig]:
    """Built-in session definitions so the wizard works without Helm config.

    These carry only broker-level config (cliType, transportAdapter).
    Helm values merge on top when running in Kubernetes.
    """
    return {
        "skuldClaude": SessionDefinitionConfig(
            enabled=True,
            display_name="Claude Code",
            description="Anthropic Claude — full IDE with terminal, tools, and MCP",
            labels=["session", "claude"],
            default_model="claude-sonnet-4-6",
            compatible_providers=["anthropic"],
            defaults={
                "broker": {
                    "cliType": "claude",
                    "transport": "sdk",
                    "transportAdapter": "skuld.transports.sdk.SDKTransport",
                    "skipPermissions": True,
                    "agentTeams": False,
                },
            },
        ),
        "skuldCodex": SessionDefinitionConfig(
            enabled=True,
            display_name="OpenAI Codex",
            description="OpenAI Codex — WebSocket protocol with streaming and tools",
            labels=["session", "codex"],
            default_model="",
            compatible_providers=["openai"],
            defaults={
                "broker": {
                    "cliType": "codex-ws",
                    "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "skipPermissions": True,
                    "agentTeams": False,
                },
            },
        ),
        "skuldCodexExec": SessionDefinitionConfig(
            enabled=True,
            display_name="OpenAI Codex (Batch)",
            description=(
                "OpenAI Codex — subprocess transport tuned for autonomous workflow "
                "execution"
            ),
            labels=["session", "codex", "batch"],
            default_model="",
            compatible_providers=["openai"],
            defaults={
                "broker": {
                    "cliType": "codex",
                    "transportAdapter": "skuld.transports.codex.CodexSubprocessTransport",
                    "skipPermissions": True,
                    "agentTeams": False,
                },
            },
        ),
        "skuldOpenCode": SessionDefinitionConfig(
            enabled=True,
            display_name="OpenCode",
            description="Model-neutral AI coding agent — Claude, OpenAI, Gemini, local",
            labels=["session", "opencode"],
            default_model="",
            compatible_providers=[],
            defaults={
                "broker": {
                    "cliType": "opencode",
                    "transportAdapter": "skuld.transports.opencode.OpenCodeHttpTransport",
                    "skipPermissions": True,
                    "agentTeams": False,
                },
            },
        ),
    }


class ProfileConfig(BaseModel):
    """Configuration for a single forge profile."""

    name: str
    description: str = ""
    workload_type: str = "session"
    model: str | None = None
    system_prompt: str | None = None
    resource_config: dict[str, Any] = Field(default_factory=dict)
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    env_vars: dict[str, str] = Field(default_factory=dict)
    env_secret_refs: list[str] = Field(default_factory=list)
    workload_config: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    session_definition: str | None = None


class TemplateConfig(BaseModel):
    """Configuration for a single workspace template (unified blueprint)."""

    name: str
    description: str = ""
    # Workspace config
    repos: list[dict[str, Any]] = Field(default_factory=list)
    setup_scripts: list[str] = Field(default_factory=list)
    workspace_layout: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    # Runtime config (merged from ProfileConfig)
    workload_type: str = "session"
    model: str | None = None
    system_prompt: str | None = None
    resource_config: dict[str, Any] = Field(default_factory=dict)
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    env_vars: dict[str, str] = Field(default_factory=dict)
    env_secret_refs: list[str] = Field(default_factory=list)
    workload_config: dict[str, Any] = Field(default_factory=dict)
    session_definition: str | None = None
    # Deprecated: kept for backward compatibility during migration
    profile_name: str | None = None


class ChronicleConfig(BaseModel):
    """Chronicle feature configuration."""

    auto_create_on_stop: bool = Field(default=True)
    summary_model: str = Field(default="claude-haiku-4-5-20251001")
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
          - adapter: "volundr.adapters.outbound.contributors.TemplateContributor"
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
        IntegrationDefinitionConfig(
            slug="volundr",
            name="Volundr API",
            description="Volundr API connection — PAT for session-to-control-plane auth",
            integration_type="code_forge",
            icon="volundr",
            credential_schema={
                "required": ["token"],
                "properties": {
                    "token": {
                        "label": "Personal Access Token",
                        "type": "password",
                        "description": "PAT for authenticating to the Volundr API",
                    },
                },
            },
            env_from_credentials={"VOLUNDR_API_TOKEN": "token"},
            auth_type="pat",
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


class ObservatoryConfig(BaseModel):
    """Observatory plugin configuration."""

    guild: ObservatoryGuildConfig = Field(default_factory=ObservatoryGuildConfig)


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
    cors: CorsConfig = Field(default_factory=CorsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    pod_manager: PodManagerConfig = Field(default_factory=PodManagerConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    niuu: InstanceRegistryConfig = Field(default_factory=InstanceRegistryConfig)
    chronicle: ChronicleConfig = Field(default_factory=ChronicleConfig)
    archive_store: ArchiveStoreConfig = Field(default_factory=ArchiveStoreConfig)
    event_pipeline: EventPipelineConfig = Field(default_factory=EventPipelineConfig)
    sleipnir: SleipnirConfig = Field(default_factory=SleipnirConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    authorization: AuthorizationConfig = Field(default_factory=AuthorizationConfig)
    credential_store: CredentialStoreConfig = Field(default_factory=CredentialStoreConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    secret_injection: SecretInjectionConfig = Field(default_factory=SecretInjectionConfig)
    resource_provider: ResourceProviderConfig = Field(default_factory=ResourceProviderConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    webhooks: WebhooksConfig = Field(default_factory=WebhooksConfig)
    linear: LinearConfig = Field(default_factory=LinearConfig)
    pat: PATConfig = Field(default_factory=PATConfig)
    auth_discovery: AuthDiscoveryConfig = Field(default_factory=AuthDiscoveryConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    oauth: OAuthConfig = Field(default_factory=OAuthConfig)
    provisioning: ProvisioningConfig = Field(default_factory=ProvisioningConfig)
    local_git: LocalGitConfig = Field(default_factory=LocalGitConfig)
    local_mounts: LocalMountsConfig = Field(default_factory=LocalMountsConfig)
    telegram_ingress: TelegramIngressConfig = Field(default_factory=TelegramIngressConfig)
    session_contributors: list[SessionContributorConfig] = Field(default_factory=list)
    session_definitions: dict[str, SessionDefinitionConfig] = Field(
        default_factory=_default_session_definitions,
        description="Session definitions keyed by name (e.g. skuldClaude, skuldCodex).",
    )
    bifrost: VolundrBifrostConfig = Field(default_factory=VolundrBifrostConfig)
    default_definition: str = Field(
        default="skuldClaude",
        description="Fallback definition key when no explicit definition is specified.",
    )
    profiles: list[ProfileConfig] = Field(default_factory=list)
    templates: list[TemplateConfig] = Field(default_factory=list)
    mcp_servers: list[MCPServerEntry] = Field(default_factory=list)
    features: list[FeatureModuleConfig] = Field(
        default_factory=_default_feature_modules,
        description="Feature module catalog — defines available UI modules.",
    )
    ravn: RavnConfig = Field(default_factory=RavnConfig)
    observatory: ObservatoryConfig = Field(default_factory=ObservatoryConfig)

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
