"""Configuration for the Mímir service.

``MimirServiceConfig`` loads from (first wins):

1. explicit constructor arguments (the ``serve`` CLI flags)
2. ``MIMIR__``-prefixed environment variables with ``__`` nesting, e.g.
   ``MIMIR__BACKEND='{"adapter": "mimir.adapters.gbrain.GBrainMimirAdapter", ...}'``,
   ``MIMIR__EVAL_CAPTURE=false``
3. a YAML config file: ``$MIMIR_CONFIG`` if set, else ``./mimir.yaml`` or
   ``/etc/mimir/config.yaml``

This makes every setting reachable in every deployment mode — including the
platform plugin, which constructs the config with no arguments.
"""

from __future__ import annotations

import os
from pathlib import Path as _Path
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from niuu.domain.observability import ObservabilityConfig
from niuu.domain.tenancy import DEFAULT_PLATFORM_TENANT_ID


def _config_paths() -> list[_Path]:
    env = os.environ.get("MIMIR_CONFIG")
    if env:
        return [_Path(env)]
    return [
        _Path("./mimir.yaml"),
        _Path("/etc/mimir/config.yaml"),
    ]


CONFIG_PATHS = _config_paths()


class LiveActivityConfig(BaseModel):
    """In-memory live read/write activity window for the 3D memory UI.

    Backs ``GET /mimir/activity/live`` (``mimir.live_activity.LiveActivityRecorder``).
    This is presence, not the durable ``/activity`` log — process-local,
    never persisted, and bounded so it cannot grow without limit.
    """

    buffer_size: int = Field(
        default=2000,
        ge=1,
        description="Maximum number of recent read/write events retained in memory.",
    )
    window_seconds: int = Field(
        default=900,
        ge=1,
        description="Only events within this many seconds of now are ever returned.",
    )


class MimirObservabilityConfig(ObservabilityConfig):
    """OpenTelemetry settings with Mímir's stable service identity."""

    service_name: str = Field(default="mimir")


class FactsTrustConfig(BaseModel):
    """Who earns which trust level for the typed fact surface.

    Trust comes from the authenticated identity, never from a request. The
    levels are ``agent`` < ``tool`` < ``operator`` < ``person``.
    """

    role_levels: dict[str, str] = Field(
        default={
            "volundr:admin": "person",
            "admin": "person",
            "volundr:developer": "person",
            "mimir:curator": "operator",
        },
        description=(
            "Human role -> trust level. A person holding several roles gets the highest. "
            "A human with none of these is refused, not given a default. Facts a person "
            "writes are 'confirmed by a person'; a role mapped to 'operator' marks "
            "curated knowledge."
        ),
    )
    scope_levels: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Workload scope -> trust level, for a resident or session acting for a user. "
            "Workloads are 'agent' unless a scope here maps to 'tool'; a level above "
            "'tool' is rejected at startup, since a resident's own writing never "
            "reaches the operator or person level."
        ),
    )


class MimirFactsConfig(BaseModel):
    """Typed facts (``mimir_fact_*`` tools). Omit the whole section to run without them."""

    adapter: str = Field(
        default="mimir.adapters.gbrain_facts.GBrainFactAdapter",
        description=(
            "Fully-qualified MimirFactPort class. gbrain is the default and the shipped "
            "backend: embedded (PGlite) on a local install, PostgreSQL on a cluster, "
            "both via the gbrain instance the knowledge deployment provisions."
        ),
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Constructor kwargs for `adapter`: for gbrain, mcp_url, api_token_file and "
            "signing_key_file (keep secrets in files)."
        ),
    )
    trust: FactsTrustConfig = Field(default_factory=FactsTrustConfig)
    chars_per_token: int = Field(
        default=4,
        ge=1,
        description=(
            "Characters per token that context_pack budgets with. gbrain budgets "
            "the same way (a character-count estimate, not a tokenizer)."
        ),
    )


class MemoryProvisioningConfig(BaseModel):
    """Per-org memory systems provisioned on an org's first use (``memory.provisioning``).

    An org not in ``memory.tenants`` gets its own gbrain, deployed through the knowledge
    deployment port, when a member of the org first uses memory: a session Forge
    confirmed, or a person through their own login. A connector names the org itself and
    never starts one. Until it answers, requests for the org get a 503 with
    ``Retry-After`` saying its memory is being prepared.
    """

    deployment: dict[str, Any] = Field(
        description=(
            "The deployment target, a dynamic mimir.ports.deployment.KnowledgeDeploymentPort "
            "adapter that provisions memory systems: {adapter: "
            "mimir.adapters.flux.FluxKnowledgeDeploymentAdapter, namespace: memory, "
            "source_name: niuu, chart_versions: {gbrain: ...}, images: {gbrain: ...}} on "
            "Kubernetes, or {adapter: mimir.adapters.local.LocalKnowledgeDeploymentAdapter, "
            "root: ..., gbrain_command: [...]} on this computer. The other keys are its "
            "constructor arguments."
        ),
    )
    retry_after_seconds: float = Field(
        default=30.0,
        gt=0,
        description=(
            "What a request for an org whose memory system is being prepared is told to wait "
            "(its Retry-After). A deployment takes minutes on Kubernetes (a database and a "
            "gbrain start), seconds on this computer."
        ),
    )
    backend: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Extra mimir.backends.gbrain.GBrainBackend arguments for every provisioned "
            "system (timeout_seconds, page_size, recall_limit, timeline_limit, ...). Its "
            "mcp_url, admin token and signing key come from the deployment and cannot be "
            "set here."
        ),
    )

    @field_validator("deployment")
    @classmethod
    def _deployment_named(cls, deployment: dict[str, Any]) -> dict[str, Any]:
        if not str(deployment.get("adapter") or "").strip():
            raise ValueError(
                "memory.provisioning.deployment has no 'adapter': name a "
                "KnowledgeDeploymentPort that provisions memory systems, e.g. "
                "mimir.adapters.flux.FluxKnowledgeDeploymentAdapter"
            )
        return deployment

    @field_validator("backend")
    @classmethod
    def _backend_without_connection(cls, backend: dict[str, Any]) -> dict[str, Any]:
        owned = sorted(
            set(backend)
            & {
                "adapter",
                "mcp_url",
                "base_url",
                "admin_token",
                "admin_token_file",
                "signing_key",
                "signing_key_file",
            }
        )
        if owned:
            raise ValueError(
                f"memory.provisioning.backend sets {', '.join(owned)}, which each provisioned "
                "system's deployment supplies; remove them"
            )
        return backend


class MemoryGatewayConfig(BaseModel):
    """The memory gateway (docs/design/memory): sessions' ``memory`` tools over their layers,
    the context pack, the people routes for search, proposals and promotions, and retention.

    Off unless ``enabled``. When enabled, every setting below is checked at startup:
    a system that cannot be built, a tenant naming a system that does not exist, a
    system lacking a capability its layers need, or a missing service
    credential stops the service.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Serve the memory routes (/memory/tools, /memory/context-pack, /memory/proposals, "
            "/memory/promotions, /memory/search, /memory/graph) on the Mímir API prefixes, and "
            "sweep expired layers."
        ),
    )
    forge_base_url: str = Field(
        default="",
        description=(
            "Forge's base URL. The gateway asks Forge who each session is on every call "
            "(GET /api/v1/forge/sessions/{id}/memory-context) with the session's own "
            "credential. Required when enabled."
        ),
    )
    forge_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="Timeout of each memory-context call to Forge; a timeout is a 401.",
    )
    systems: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Memory systems, each a dynamic adapter with a gateway-local `name`: "
            "{name: acme-brain, adapter: mimir.backends.gbrain.GBrainBackend, mcp_url: ..., "
            "api_token_file: ..., signing_key_file: ...}. `name` is not passed to the "
            "constructor; the other keys are."
        ),
    )
    tenants: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Tenant id -> the name of the system that holds that org's layers (one memory "
            "system per org). The operator's override: a listed org always uses its system. "
            "An org not listed is refused, naming this setting, unless memory.provisioning "
            "is set."
        ),
    )
    provisioning: MemoryProvisioningConfig | None = Field(
        default=None,
        description=(
            "Provision a memory system for an org that is not in `tenants` on its first use "
            "(docs/operator/mimir.md, Provisioned memory systems). None (the default): no "
            "provisioning, an unmapped org is refused."
        ),
    )
    default_result_count: int = Field(
        default=10,
        ge=1,
        description=(
            "How many changes the change feed (the context pack's changes since the owner's "
            "previous session) asks of a memory system per call. A page size, not a cap: the "
            "feed is read until it has no more."
        ),
    )
    service_auth: dict[str, Any] | None = Field(
        default=None,
        description=(
            "How the gateway authenticates as a service, to Forge's "
            "POST /api/v1/forge/memory/ended (the retention sweep): a dynamic "
            "niuu.ports.http_auth.HttpAuthPort adapter, e.g. {adapter: "
            "niuu.adapters.outbound.http_auth.WorkloadIdentityBearerTokenAuthAdapter, "
            "audiences: [forge]} on a cluster, or {adapter: "
            "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter} in mini mode without auth. "
            "Required when enabled."
        ),
    )
    connector_service_subjects: list[str] = Field(
        default_factory=list,
        description=(
            "Workload subjects (the actor, `act.sub`, of a verified workload-identity token) "
            "that may write connector sources through POST {base}/memory/sources, e.g. "
            "[ting]. Empty means no caller is a connector: where Mímir's identity adapter "
            "verifies callers the route answers 403 naming this setting. With the allow-all "
            "identity of local mini mode nothing verifies callers, so the local caller passes "
            "(still never with a session or scoped credential)."
        ),
    )
    retention_days: float = Field(
        default=14,
        ge=0,
        description=(
            "Days a run or session layer outlives its run or session (design decision "
            "2026-11-12), after which the retention sweep deletes it. A layer whose session "
            "or run Forge no longer knows is deleted at once."
        ),
    )
    retention_sweep_seconds: float = Field(
        default=3600.0,
        gt=0,
        description=(
            "Seconds between retention sweeps. A failed sweep is logged at error, counted "
            "(memory.retention_sweep on the degraded counter) and tried again after this long."
        ),
    )
    graph_cache_seconds: float = Field(
        default=30.0,
        ge=0,
        description=(
            "How stale the knowledge graph (GET /memory/graph) may be: a graph built for an org "
            "and a set of layers is served again for this many seconds before it is rebuilt "
            "from the memory system. 0 keeps nothing and rebuilds on every request."
        ),
    )
    graph_concurrency: int = Field(
        default=8,
        ge=1,
        description=(
            "Reads against the memory system in flight at once while one knowledge graph is "
            "built (page, timeline and source reads). Bounded by what the memory system's "
            "endpoint serves in parallel; builds are slower, never wrong, with a lower value."
        ),
    )
    org_admin_roles: list[str] = Field(
        default_factory=lambda: ["volundr:admin"],
        min_length=1,
        description=(
            "Roles of a person's login that make them an org admin, who decides proposals "
            "to the org layer."
        ),
    )

    @field_validator("connector_service_subjects")
    @classmethod
    def _subjects_named(cls, subjects: list[str]) -> list[str]:
        if any(not subject.strip() for subject in subjects):
            raise ValueError(
                "memory.connector_service_subjects has a blank entry: name each connector "
                "service's workload subject (e.g. ting) or leave the list out"
            )
        return [subject.strip() for subject in subjects]


class MimirServiceConfig(BaseSettings):
    """Configuration for a Mímir service instance (standalone or plugin).

    See the module docstring for the YAML/env/constructor source order.
    """

    model_config = SettingsConfigDict(
        yaml_file=CONFIG_PATHS,
        yaml_file_encoding="utf-8",
        env_prefix="MIMIR__",
        env_nested_delimiter="__",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    backend: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The store this service serves, as a dynamic adapter: the fully-qualified "
            "niuu.ports.mimir.MimirPort class under 'adapter' and its constructor "
            "arguments as the other keys, e.g. {adapter: "
            "mimir.adapters.gbrain.GBrainMimirAdapter, mcp_url: http://localhost:3131/mcp}. "
            "Required: there is no default store, and a service without one fails at "
            "startup naming this setting."
        ),
    )
    path: str = Field(
        default="~/.ravn/mimir",
        description=(
            "Directory for the service's own state: the mount registry "
            "(<path>/.mimir-registry.json) and eval captures (<path>/evals/). "
            "It holds no knowledge; that lives in `backend`."
        ),
    )
    host: str = Field(
        default="0.0.0.0",
        description="Host address to bind the service to.",
    )
    port: int = Field(
        default=7477,
        description="Port to bind the service to.",
    )
    name: str = Field(
        default="local",
        description="Instance name shown in the mount registry.",
    )
    role: str = Field(
        default="local",
        description="Instance role: 'shared', 'local', or 'domain'.",
    )
    categories: list[str] | None = Field(
        default=None,
        description="Category filter for domain-scoped Mímirs. None means all categories.",
    )
    platform_tenant_id: str = Field(
        default=DEFAULT_PLATFORM_TENANT_ID,
        description=(
            "The org that runs this deployment. It alone reaches this instance's own "
            "knowledge and the global mounts; other orgs reach only their own mounts."
        ),
    )
    tenant_id: str = Field(default="", description="Owning tenant; enforced on instance API calls.")

    auth_mode: str = Field(
        default="envoy",
        validation_alias=AliasChoices("AUTH_MODE", "auth_mode"),
        description=(
            "How this Mímir instance trusts identity: 'envoy' (default — an Envoy "
            "sidecar, or the shared niuu host process, has already verified the "
            "caller and forwards trusted x-auth-* headers), 'none' (explicit "
            "no-auth), or 'oidc' (in-process JWT verification via JWKS, for a host "
            "without Envoy). Set by the mini/docker CLI host from host_auth.mode "
            "(cli.config.AuthConfig) via the AUTH_MODE env var — the same variable "
            "every other co-hosted service reads "
            "(niuu.service_runtime._get_auth_mode). Bypasses the MIMIR__ env "
            "prefix deliberately so one host-wide setting covers every service."
        ),
    )
    identity_adapter: str = Field(
        default="identity.adapters.identity.EnvoyHeaderAuthenticationAdapter",
        validation_alias=AliasChoices("MIMIR_AUTH__ADAPTER", "identity_adapter"),
        description=(
            "Fully-qualified niuu.ports.identity.HeaderAuthenticationPort class used "
            "to extract a verified Principal from inbound requests (_require_deploy_auth, "
            "enforce_instance_tenant, and per-request tenant scoping). Validated against "
            "auth_mode at startup by niuu.service_runtime._validate_identity_adapter_class — "
            "an Envoy-trusting adapter cannot be configured for auth_mode: oidc or none, "
            "and vice versa. Set by the CLI host via the MIMIR_AUTH__ADAPTER env var."
        ),
    )
    identity_kwargs: dict = Field(
        default_factory=dict,
        validation_alias=AliasChoices("MIMIR_AUTH__KWARGS", "identity_kwargs"),
        description=(
            "Kwargs for identity_adapter (e.g. oidc issuers for JwksBearerAuthenticationAdapter). "
            "Set by the CLI host via the MIMIR_AUTH__KWARGS env var (JSON)."
        ),
    )

    announce_url: str | None = Field(
        default=None,
        description=(
            "Public URL this service is reachable at. When set, the mount registry "
            "lists this instance as a remote mount at that URL, and registry "
            "entries for deployed remote knowledge mounts point at it. "
            "None = a local mount."
        ),
    )
    eval_capture: bool = Field(
        default=True,
        description=(
            "Append every /mimir/search query and its result paths to "
            "<path>/evals/queries-YYYY-Www.jsonl. Feeds the Analytics query "
            "traffic view and offline replay (python -m mimir eval replay). "
            "On by default; disable for privacy-sensitive deployments."
        ),
    )
    deployment: dict | None = Field(
        default=None, description="Dynamic knowledge deployment adapter and constructor kwargs."
    )
    facts: MimirFactsConfig | None = Field(
        default=None,
        description=(
            "Typed facts with provenance and trust, served as the mimir_fact_* MCP tools. "
            "None serves no fact tools: an operator's choice to run without them. When set "
            "and the backend cannot be built, startup fails."
        ),
    )
    memory: MemoryGatewayConfig = Field(
        default_factory=MemoryGatewayConfig,
        description="The memory gateway: layered memory tools for Forge sessions.",
    )
    live_activity: LiveActivityConfig = Field(
        default_factory=LiveActivityConfig,
        description="In-memory live read/write activity window (GET /activity/live).",
    )
    observability: MimirObservabilityConfig = Field(default_factory=MimirObservabilityConfig)
