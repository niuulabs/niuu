"""Bifröst configuration: providers, aliases, routing settings, auth, and quotas."""

from __future__ import annotations

import fnmatch
import os
import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from bifrost.auth import AuthMode
from niuu.domain.model_catalog import ManagedModelProvider, ManagedModelTier


class RoutingStrategy(StrEnum):
    """How the router selects and orders provider candidates for a request."""

    DIRECT = "direct"
    """Try only the first configured provider for the model. No failover."""

    FAILOVER = "failover"
    """Try the primary provider; fall back to alternatives on retryable errors."""

    COST_OPTIMISED = "cost_optimised"
    """Order providers by cost_per_token ascending (cheapest first)."""

    ROUND_ROBIN = "round_robin"
    """Cycle through all providers that serve the model on each request."""

    LATENCY_OPTIMISED = "latency_optimised"
    """Order providers by recent P99 latency ascending (fastest first)."""


class ProviderConfig(BaseModel):
    """Configuration for a single LLM provider."""

    api_key_env: str = Field(default="", description="Environment variable holding the API key.")
    api_key_file: str = Field(
        default="",
        description=(
            "Path to a file whose contents are the API key. "
            "Used as a fallback when api_key_env is absent or empty. "
            "Suitable for Kubernetes secret mounts."
        ),
    )
    base_url: str = Field(default="", description="Base URL for the provider's API.")
    models: list[str] = Field(
        default_factory=list, description="Models supported by this provider."
    )
    timeout: float = Field(default=120.0, description="Request timeout in seconds.")
    chat_template_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Chat-template arguments enforced on requests to OpenAI-compatible providers. "
            "Provider values override caller-supplied values."
        ),
    )
    cost_per_token: float = Field(
        default=0.0,
        description=(
            "Relative cost per token (arbitrary units). Used by the cost_optimised "
            "routing strategy to prefer cheaper providers. Lower is cheaper."
        ),
    )

    @property
    def api_key(self) -> str:
        if not self.api_key_env:
            return ""
        return os.environ.get(self.api_key_env, "")


class ManagedModelConfig(BaseModel):
    """Canonical Bifrost-owned model catalog entry."""

    id: str
    name: str
    vendor: str = Field(
        default="",
        description="Stable vendor/runtime family (e.g. anthropic, openai, local).",
    )
    provider: ManagedModelProvider = Field(
        default=ManagedModelProvider.CLOUD,
        description="High-level provider kind used by platform UIs (cloud/local).",
    )
    tier: ManagedModelTier = Field(
        default=ManagedModelTier.BALANCED,
        description="Tier hint used by the UI and workflow selectors.",
    )
    color: str = Field(
        default="#2563EB",
        description="UI accent color for badges and chips.",
    )
    description: str = Field(
        default="",
        description="Human-readable capabilities summary.",
    )
    cost_per_million_tokens: float | None = Field(
        default=None,
        description="Approximate blended cost per million tokens in USD.",
    )
    vram_required: str | None = Field(
        default=None,
        description="VRAM hint for local models, when applicable.",
    )
    session_definition: str | None = Field(
        default=None,
        description="Preferred runtime/session definition for this model.",
    )
    supports_tools: bool = Field(
        default=True,
        description="Whether the model is expected to support tool use.",
    )
    supports_thinking: bool = Field(
        default=True,
        description="Whether the model supports higher-latency reasoning modes.",
    )
    enabled: bool = Field(
        default=True,
        description="Whether the model should be shown to operators and consumers.",
    )


def _default_models() -> list[ManagedModelConfig]:
    """Built-in Bifrost model catalog used when no explicit catalog is configured."""
    return [
        ManagedModelConfig(
            id="claude-fable-5",
            name="Claude Fable 5",
            vendor="anthropic",
            provider=ManagedModelProvider.CLOUD,
            tier=ManagedModelTier.FRONTIER,
            color="#7C3AED",
            description=(
                "Anthropic's most capable model for demanding reasoning and "
                "long-horizon agentic work; 1M-token context."
            ),
            # Claude Fable 5 is $10 in / $50 out per 1M tokens (blended shown).
            cost_per_million_tokens=30.0,
            session_definition="skuldClaude",
            supports_tools=True,
            supports_thinking=True,
        ),
        ManagedModelConfig(
            id="claude-opus-4-8",
            name="Claude Opus 4.8",
            vendor="anthropic",
            provider=ManagedModelProvider.CLOUD,
            tier=ManagedModelTier.FRONTIER,
            color="#8B5CF6",
            description="Anthropic frontier reasoning model, 1M-token context.",
            cost_per_million_tokens=15.0,
            session_definition="skuldClaude",
            supports_tools=True,
            supports_thinking=True,
        ),
        ManagedModelConfig(
            id="claude-sonnet-5",
            name="Claude Sonnet 5",
            vendor="anthropic",
            provider=ManagedModelProvider.CLOUD,
            tier=ManagedModelTier.BALANCED,
            color="#2563EB",
            description=(
                "Anthropic balanced model — near-Opus quality for coding and agentic work."
            ),
            # Claude Sonnet 5 is $3 in / $15 out per 1M tokens (blended shown).
            cost_per_million_tokens=9.0,
            session_definition="skuldClaude",
            supports_tools=True,
            supports_thinking=True,
        ),
        ManagedModelConfig(
            id="gpt-5.6-sol",
            name="GPT-5.6 Sol",
            vendor="openai",
            provider=ManagedModelProvider.CLOUD,
            tier=ManagedModelTier.FRONTIER,
            color="#059669",
            description=(
                "OpenAI GPT-5.6 Sol flagship coding model; Völundr sessions "
                "default to high reasoning effort."
            ),
            # GPT-5.6 Sol is $5 in / $30 out per 1M tokens; use the output rate.
            cost_per_million_tokens=30.0,
            session_definition="skuldCodex",
            supports_tools=True,
            supports_thinking=True,
        ),
        ManagedModelConfig(
            id="gpt-5.6-terra",
            name="GPT-5.6 Terra",
            vendor="openai",
            provider=ManagedModelProvider.CLOUD,
            tier=ManagedModelTier.BALANCED,
            color="#14B8A6",
            description="OpenAI GPT-5.6 Terra — strong lower-cost coding model.",
            # GPT-5.6 Terra is $2.50 in / $15 out per 1M tokens; use the output rate.
            cost_per_million_tokens=15.0,
            session_definition="skuldCodex",
            supports_tools=True,
            supports_thinking=True,
        ),
        ManagedModelConfig(
            id="grok-build",
            name="Grok Build",
            vendor="xai",
            provider=ManagedModelProvider.CLOUD,
            tier=ManagedModelTier.FRONTIER,
            color="#1DA1F2",
            description="xAI Grok Build agentic coding model (ACP over stdio).",
            cost_per_million_tokens=None,  # Provider does not publish a stable blended rate
            session_definition="skuldGrok",
            supports_tools=True,
            supports_thinking=True,
        ),
        ManagedModelConfig(
            id="deepseek-v4-flash",
            name="DeepSeek V4 Flash",
            vendor="deepseek",
            provider=ManagedModelProvider.CLOUD,
            tier=ManagedModelTier.BALANCED,
            color="#4D6BFE",
            description=(
                "DeepSeek V4 Flash — fast thinking-mode coding model; the "
                "DeepSeek Harness (dsh) runtime default."
            ),
            cost_per_million_tokens=None,  # Annotate once DeepSeek pricing is pinned
            session_definition="skuldDeepSeekHarness",
            supports_tools=True,
            supports_thinking=True,
        ),
        ManagedModelConfig(
            id="deepseek-v4-pro",
            name="DeepSeek V4 Pro",
            vendor="deepseek",
            provider=ManagedModelProvider.CLOUD,
            tier=ManagedModelTier.FRONTIER,
            color="#3B5BDB",
            description=(
                "DeepSeek V4 Pro — frontier thinking-mode model for demanding "
                "agentic coding via the DeepSeek Harness (dsh) runtime."
            ),
            cost_per_million_tokens=None,  # Annotate once DeepSeek pricing is pinned
            session_definition="skuldDeepSeekHarness",
            supports_tools=True,
            supports_thinking=True,
        ),
        ManagedModelConfig(
            id="llama3.2:latest",
            name="Llama 3.2",
            vendor="local",
            provider=ManagedModelProvider.LOCAL,
            tier=ManagedModelTier.BALANCED,
            color="#F59E0B",
            description="Open source local model served through Ollama.",
            vram_required="8GB",
            session_definition="skuldOpenCode",
            supports_tools=True,
            supports_thinking=False,
        ),
        ManagedModelConfig(
            id="codellama:latest",
            name="Code Llama",
            vendor="local",
            provider=ManagedModelProvider.LOCAL,
            tier=ManagedModelTier.EXECUTION,
            color="#EF4444",
            description="Local code-specialized model served through Ollama.",
            vram_required="16GB",
            session_definition="skuldOpenCode",
            supports_tools=True,
            supports_thinking=False,
        ),
        ManagedModelConfig(
            id="deepseek-r1:latest",
            name="DeepSeek R1",
            vendor="local",
            provider=ManagedModelProvider.LOCAL,
            tier=ManagedModelTier.REASONING,
            color="#8B5CF6",
            description="Local reasoning-oriented model served through Ollama.",
            vram_required="32GB",
            session_definition="skuldOpenCode",
            supports_tools=True,
            supports_thinking=True,
        ),
    ]


class PricingOverride(BaseModel):
    """USD per million tokens for a single model (overrides built-in snapshot)."""

    input_per_million: float = 0.0
    output_per_million: float = 0.0
    cache_creation_per_million: float = 0.0
    cache_read_per_million: float = 0.0


class QuotaConfig(BaseModel):
    """Quota limits for a tenant or agent."""

    max_tokens_per_day: int = Field(
        default=0,
        description="Maximum total tokens (input + output) per day. 0 = unlimited.",
    )
    max_cost_per_day: float = Field(
        default=0.0,
        description="Maximum USD cost per day. 0.0 = unlimited.",
    )
    max_requests_per_hour: int = Field(
        default=0,
        description="Maximum requests per calendar hour. 0 = unlimited.",
    )
    soft_limit_fraction: float = Field(
        default=0.9,
        description=(
            "Fraction of any hard limit at which a warning header is injected "
            "(0 < fraction < 1). Default: 0.9 (warn at 90% of the limit)."
        ),
    )


class AgentPermissions(BaseModel):
    """Per-agent access control and optional budget."""

    allowed_models: list[str] = Field(
        default_factory=list,
        description=(
            "Models this agent is permitted to use. Empty list means all models are allowed."
        ),
    )
    quota: QuotaConfig = Field(
        default_factory=QuotaConfig,
        description="Optional per-agent quota (zero values mean no limit).",
    )


class RuleCondition(BaseModel):
    """Conditions that must all match for a rule to fire (AND semantics).

    Numeric fields accept comparison expressions like ``'<= 512'`` or ``'>= 80'``.
    Plain numbers are treated as equality checks.
    """

    model: str | None = Field(
        default=None,
        description="Match when the request model equals this alias or model ID.",
    )
    max_tokens: str | None = Field(
        default=None,
        description="Numeric comparison expression for max_tokens (e.g. '<= 512').",
    )
    thinking: bool | None = Field(
        default=None,
        description="Match when thinking is enabled (true) or disabled (false).",
    )
    agent_budget_pct: str | None = Field(
        default=None,
        description="Numeric comparison expression for remaining agent budget % (e.g. '>= 80').",
    )
    provider: str | None = Field(
        default=None,
        description="Match when the resolved primary provider name equals this value.",
    )
    has_tools: bool | None = Field(
        default=None,
        description="Match when the request includes tool definitions (true) or not (false).",
    )
    content_matches: str | None = Field(
        default=None,
        description="Regex pattern applied to the full concatenated message content.",
    )
    system_prompt_matches: str | None = Field(
        default=None,
        description="Regex pattern applied to the system prompt text.",
    )
    message_count: str | None = Field(
        default=None,
        description="Numeric comparison expression for the number of messages (e.g. '>= 10').",
    )
    has_image: bool | None = Field(
        default=None,
        description="Match when the request contains image blocks (true) or not (false).",
    )
    agent_id: str | None = Field(
        default=None,
        description=(
            "fnmatch pattern matched against the X-Ravn-Agent-Id header value. "
            "Use '*' as a wildcard (e.g. 'reviewer*' matches 'reviewer', 'reviewer-bot'). "
            "Non-empty patterns only match when the header is present."
        ),
    )

    @model_validator(mode="after")
    def _validate_regex_fields(self) -> RuleCondition:
        for field_name in ("content_matches", "system_prompt_matches"):
            pattern = getattr(self, field_name)
            if pattern is not None:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"Invalid regex in {field_name}: {exc}") from None
        return self


class RuleConfig(BaseModel):
    """A single declarative routing rule."""

    name: str = Field(description="Human-readable rule identifier (used in logs).")
    when: RuleCondition = Field(description="Conditions that must all match for the rule to fire.")
    action: Literal["route_to", "reject", "log", "tag", "strip_images"] = Field(
        description="Action to take when the rule matches.",
    )
    target: str | None = Field(
        default=None,
        description="Model or alias to route to (required for action='route_to').",
    )
    message: str | None = Field(
        default=None,
        description="Rejection message returned to the caller (for action='reject').",
    )
    tags: dict[str, str] = Field(
        default_factory=dict,
        description="Metadata key-value pairs added to the audit entry (for action='tag').",
    )

    @model_validator(mode="after")
    def _validate_action_fields(self) -> RuleConfig:
        if self.action == "route_to" and not self.target:
            raise ValueError("target is required when action is 'route_to'")
        return self


class BudgetGuardrailConfig(BaseModel):
    """Budget-based guardrail: route to cheaper model or reject when budget is exhausted."""

    warn_at_pct: float = Field(
        default=80.0,
        description=(
            "Percentage of daily budget consumed at which the warn action fires (0–100). "
            "Default: 80 (warn when 80% of the budget is used)."
        ),
    )
    warn_action: Literal["route_to"] = Field(
        default="route_to",
        description="Action to take at the warn threshold. Currently only 'route_to' is supported.",
    )
    warn_target: str = Field(
        default="fast",
        description="Model alias or ID to route to when the warn threshold is reached.",
    )
    hard_limit_action: Literal["reject"] = Field(
        default="reject",
        description=(
            "Action to take when the budget is fully exhausted (100%). Only 'reject' is supported."
        ),
    )
    degradation_chain: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered list of model IDs (most capable → cheapest) for automatic downgrade. "
            "When the warn threshold is reached, the request model is looked up in this list "
            "and downgraded to the next cheaper entry. "
            "Example: ['claude-opus-4-6', 'claude-sonnet-4-6', 'claude-haiku-4-5-20251001']. "
            "When empty, warn_target is used instead (legacy behaviour)."
        ),
    )

    @model_validator(mode="after")
    def _validate_degradation_chain(self) -> BudgetGuardrailConfig:
        seen: set[str] = set()
        for model_id in self.degradation_chain:
            if model_id in seen:
                raise ValueError(f"degradation_chain contains duplicate model ID: {model_id!r}")
            seen.add(model_id)
        return self


class ContextWindowGuardrailConfig(BaseModel):
    """Context-window guardrail: reject requests that exceed a message count threshold."""

    max_messages: int = Field(
        default=50,
        description="Maximum number of messages allowed in a single request.",
    )
    action: Literal["reject"] = Field(
        default="reject",
        description="Action to take when the message count exceeds the limit.",
    )
    reason: str = Field(
        default="Context window limit reached",
        description="Rejection message returned to the caller.",
    )


class GuardrailsConfig(BaseModel):
    """Container for all declarative guardrail policies."""

    budget: BudgetGuardrailConfig | None = Field(
        default=None,
        description=(
            "Budget-based guardrail. When set, agents approaching their daily cost limit "
            "are automatically routed to a cheaper model; exhausted agents are rejected."
        ),
    )
    context_window: ContextWindowGuardrailConfig | None = Field(
        default=None,
        description=(
            "Context-window guardrail. When set, requests with too many messages are rejected."
        ),
    )


class UsageStoreConfig(BaseModel):
    """Configuration for the usage persistence backend."""

    adapter: str = Field(
        default="memory",
        description=("Storage backend. Accepted values: 'memory' (default), 'sqlite', 'postgres'."),
    )
    path: str = Field(
        default="./bifrost_usage.db",
        description="File path for the SQLite backend (ignored for other adapters).",
    )
    dsn: str = Field(
        default="",
        description=(
            "PostgreSQL DSN for the postgres backend (ignored for other adapters). "
            "Falls back to the BIFROST_USAGE_DSN environment variable when blank."
        ),
    )
    dsn_env: str = Field(
        default="BIFROST_USAGE_DSN",
        description="Environment variable holding the PostgreSQL DSN (used when dsn is blank).",
    )

    def effective_dsn(self) -> str:
        """Return the DSN, falling back to the configured environment variable."""
        return self.dsn or os.environ.get(self.dsn_env, "")


class KeyVaultConfig(BaseModel):
    """Configuration for the provider key vault."""

    secrets_file: str = Field(
        default="",
        description=(
            "Path to a YAML file mapping provider names to API keys. "
            "When set, keys are loaded from this file instead of (or in "
            "addition to) environment variables. "
            "File format: ``provider_name: api_key_value``."
        ),
    )


class EventsConfig(BaseModel):
    """Configuration for the Valkyrie cost event emitter."""

    adapter: str = Field(
        default="null",
        description="Event emitter adapter. Accepted values: 'null' (default), 'sleipnir'.",
    )
    url: str = Field(
        default="",
        description=(
            "AMQP URL for the Sleipnir (RabbitMQ) adapter (e.g. amqp://guest:guest@localhost/)."
        ),
    )
    exchange: str = Field(
        default="bifrost.events",
        description="RabbitMQ exchange name.",
    )
    exchange_type: str = Field(
        default="topic",
        description="RabbitMQ exchange type.",
    )
    budget_warning_threshold_pct: float = Field(
        default=20.0,
        description=(
            "Remaining budget percentage at or below which a budget_warning event is "
            "emitted. Only applies when the agent has a configured daily cost limit."
        ),
    )


class AuditDetailLevel(StrEnum):
    """How much detail to capture in each audit log entry."""

    MINIMAL = "minimal"
    """Timestamp, agent_id, model, tokens, cost, latency only."""

    STANDARD = "standard"
    """Minimal + provider, session/saga IDs, outcome, status code, rule metadata."""

    FULL = "full"
    """Standard + prompt content and response content."""


class OtelAuditConfig(BaseModel):
    """OpenTelemetry exporter settings for the OTel audit adapter."""

    endpoint: str = Field(
        default="http://localhost:4317",
        description="OTLP gRPC endpoint (e.g. http://otel-collector:4317).",
    )
    service_name: str = Field(
        default="bifrost",
        description="Service name embedded in the OTel Resource.",
    )


class AuditAdapter(StrEnum):
    """Supported audit logging backends."""

    NULL = "null"
    """No-op — discard all audit events (default)."""
    POSTGRES = "postgres"
    """Write audit events to PostgreSQL."""
    SQLITE = "sqlite"
    """Write audit events to a local SQLite database."""
    OTEL = "otel"
    """Emit audit events as OpenTelemetry spans."""


class AuditConfig(BaseModel):
    """Configuration for the request audit log."""

    adapter: AuditAdapter = Field(
        default=AuditAdapter.NULL,
        description=(
            "Audit backend. Accepted values: 'null' (default, no-op), 'postgres', 'sqlite', 'otel'."
        ),
    )
    level: AuditDetailLevel = Field(
        default=AuditDetailLevel.MINIMAL,
        description=(
            "Detail level for each audit entry. "
            "'minimal' logs timestamp/agent/model/tokens/cost/latency; "
            "'standard' adds provider/session/outcome/rule metadata; "
            "'full' also captures prompt and response content."
        ),
    )
    dsn: str = Field(
        default="",
        description=(
            "PostgreSQL DSN for the postgres audit backend. "
            "Falls back to the BIFROST_AUDIT_DSN environment variable when blank."
        ),
    )
    dsn_env: str = Field(
        default="BIFROST_AUDIT_DSN",
        description="Environment variable holding the PostgreSQL DSN (used when dsn is blank).",
    )
    path: str = Field(
        default="./bifrost_audit.db",
        description="File path for the SQLite audit backend (ignored for other adapters).",
    )
    otel: OtelAuditConfig = Field(
        default_factory=OtelAuditConfig,
        description="OpenTelemetry exporter settings (used when adapter='otel').",
    )
    retention_days: int = Field(
        default=90,
        description=(
            "Number of days to retain audit records. "
            "Applies to adapters that support pruning (sqlite, postgres). "
            "0 = retain indefinitely."
        ),
    )

    def effective_dsn(self) -> str:
        """Return the DSN, falling back to the configured environment variable."""
        return self.dsn or os.environ.get(self.dsn_env, "")


class CacheMode(StrEnum):
    """Which cache backend to use."""

    REDIS = "redis"
    """Shared Redis cache — suitable for multi-instance infra deployments."""

    MEMORY = "memory"
    """In-process LRU cache — suitable for standalone / Pi-mode deployments."""

    DISABLED = "disabled"
    """No caching (default). Every request is forwarded to the provider."""


class CacheConfig(BaseModel):
    """Configuration for the semantic response cache."""

    mode: CacheMode = Field(
        default=CacheMode.DISABLED,
        description=(
            "Cache backend: 'redis' (shared, multi-instance), "
            "'memory' (in-process LRU), or 'disabled' (no cache, default)."
        ),
    )
    redis_url: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL (used when mode='redis').",
    )
    default_ttl: int = Field(
        default=300,
        description="Default cache entry time-to-live in seconds.",
    )
    max_memory_entries: int = Field(
        default=1000,
        description="Maximum entries kept in the in-memory LRU cache (mode='memory').",
    )


# Default base URLs per provider type.
_DEFAULT_BASE_URLS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "ollama": "http://localhost:11434",
}


class BifrostConfig(BaseModel):
    """Top-level Bifröst gateway configuration."""

    providers: dict[str, ProviderConfig] = Field(
        default_factory=dict,
        description="Map of provider name → provider config.",
    )
    models: list[ManagedModelConfig] = Field(
        default_factory=_default_models,
        description="Canonical Bifrost-owned model catalog used across the platform.",
    )
    aliases: dict[str, str] = Field(
        default_factory=dict,
        description="Model alias → canonical model name.",
    )
    routing_strategy: RoutingStrategy = Field(
        default=RoutingStrategy.FAILOVER,
        description=(
            "How to select and order provider candidates. "
            "Defaults to 'failover' for backwards compatibility."
        ),
    )
    model_routing_strategies: dict[str, RoutingStrategy] = Field(
        default_factory=dict,
        description=(
            "Per-model or per-alias routing strategy overrides. "
            "Keys are model IDs or alias names; values are routing strategy names. "
            "When a model matches a key, its strategy overrides the global routing_strategy. "
            "Example: {'claude-sonnet-4-6': 'failover', 'fast': 'round_robin'}"
        ),
    )
    latency_ewma_alpha: float = Field(
        default=0.2,
        description=(
            "Smoothing factor for the EWMA latency estimator used by the "
            "latency_optimised strategy (0 < alpha <= 1). Higher values weight "
            "recent observations more heavily."
        ),
    )
    host: str = Field(default="0.0.0.0", description="Host to bind the gateway server.")
    port: int = Field(default=8088, description="Port to bind the gateway server.")

    # ── Authentication ───────────────────────────────────────────────────────
    auth_mode: AuthMode = Field(
        default=AuthMode.OPEN,
        description=(
            "Authentication mode: 'open' (trust headers), "
            "'pat' (Bearer JWT), or 'mesh' (Envoy injected headers)."
        ),
    )
    pat_secret: str = Field(
        default="",
        description=(
            "HS256 signing secret for PAT verification. "
            "Required when auth_mode = 'pat'. "
            "Read from the PAT_SECRET environment variable if blank."
        ),
    )

    # ── Pricing overrides ───────────────────────────────────────────────────
    pricing: dict[str, PricingOverride] = Field(
        default_factory=dict,
        description=(
            "Per-model pricing overrides (USD / million tokens). "
            "Unspecified models fall back to the built-in snapshot."
        ),
    )
    pricing_file: str = Field(
        default="",
        description=(
            "Path to a YAML file containing per-model pricing (USD / million tokens). "
            "Loaded at startup and merged with the built-in snapshot. "
            "Inline ``pricing`` overrides take precedence over this file. "
            "Format: ``model_id: {input_per_million, output_per_million, "
            "cache_creation_per_million, cache_read_per_million}``."
        ),
    )

    # ── Quota enforcement ────────────────────────────────────────────────────
    default_quota: QuotaConfig = Field(
        default_factory=QuotaConfig,
        description="Default quota applied to all tenants (zero values = unlimited).",
    )
    tenant_quotas: dict[str, QuotaConfig] = Field(
        default_factory=dict,
        description="Per-tenant quota overrides (keyed by tenant_id).",
    )
    agent_permissions: dict[str, AgentPermissions] = Field(
        default_factory=dict,
        description="Per-agent model access control and optional budget.",
    )

    # ── Guardrails ───────────────────────────────────────────────────────────
    guardrails: GuardrailsConfig = Field(
        default_factory=GuardrailsConfig,
        description=(
            "Declarative guardrail policies (budget routing, context-window limits). "
            "Evaluated before the routing rule engine on every request."
        ),
    )

    # ── Routing rules ────────────────────────────────────────────────────────
    rules: list[RuleConfig] = Field(
        default_factory=list,
        description=(
            "Declarative routing rules evaluated in order before the routing strategy runs. "
            "First match wins."
        ),
    )

    # ── Usage storage ────────────────────────────────────────────────────────
    usage_store: UsageStoreConfig = Field(
        default_factory=UsageStoreConfig,
        description="Configuration for the usage persistence backend.",
    )

    # ── Key vault ────────────────────────────────────────────────────────────
    key_vault: KeyVaultConfig = Field(
        default_factory=KeyVaultConfig,
        description=(
            "Provider API key vault configuration. "
            "Keys are loaded at startup and never returned in responses or logs."
        ),
    )

    # ── Event emission ───────────────────────────────────────────────────────
    events: EventsConfig = Field(
        default_factory=EventsConfig,
        description="Configuration for the Valkyrie cost event emitter.",
    )

    # ── Response cache ────────────────────────────────────────────────────────
    cache: CacheConfig = Field(
        default_factory=CacheConfig,
        description=(
            "Semantic response cache configuration. "
            "Caches non-streaming LLM responses by SHA-256(tenant+model+system+messages). "
            "Hits are recorded in accounting with cost=0."
        ),
    )

    # ── Audit logging ─────────────────────────────────────────────────────────
    audit: AuditConfig = Field(
        default_factory=AuditConfig,
        description=(
            "Request audit log configuration. "
            "Appends one entry per LLM request with configurable detail level. "
            "Default adapter is 'null' (no audit logging)."
        ),
    )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def resolve_alias(self, model: str) -> str:
        """Expand a model alias to its canonical name."""
        return self.aliases.get(model, model)

    def model_entry(self, model: str) -> ManagedModelConfig | None:
        """Return the configured catalog entry for a model or alias."""
        canonical = self.resolve_alias(model)
        for entry in self.models:
            if entry.id == canonical:
                return entry
        return None

    def provider_for_model(self, model: str) -> str | None:
        """Return the provider name that owns *model*, or None if unknown."""
        for name, cfg in self.providers.items():
            if model in cfg.models:
                return name
        canonical = self.resolve_alias(model)
        for name, cfg in self.providers.items():
            if canonical in cfg.models:
                return name
        return None

    def providers_for_model(self, model: str) -> list[str]:
        """Return all provider names that serve *model*, in config order."""
        canonical = self.resolve_alias(model)
        return [name for name, cfg in self.providers.items() if canonical in cfg.models]

    def effective_base_url(self, provider_name: str) -> str:
        """Return the effective base URL for a provider, using defaults when absent."""
        cfg = self.providers.get(provider_name)
        if cfg and cfg.base_url:
            return cfg.base_url
        return _DEFAULT_BASE_URLS.get(provider_name, "")

    def effective_pat_secret(self) -> str:
        """Return the PAT signing secret, falling back to the PAT_SECRET env var."""
        return self.pat_secret or os.environ.get("PAT_SECRET", "")

    def routing_strategy_for_model(self, model: str) -> RoutingStrategy:
        """Return the effective routing strategy for *model*.

        Checks ``model_routing_strategies`` first (exact match on both the
        raw model name/alias and the resolved canonical name), then falls
        back to the global ``routing_strategy``.
        """
        if model in self.model_routing_strategies:
            return self.model_routing_strategies[model]
        canonical = self.resolve_alias(model)
        if canonical in self.model_routing_strategies:
            return self.model_routing_strategies[canonical]
        return self.routing_strategy

    def quota_for_tenant(self, tenant_id: str) -> QuotaConfig:
        """Return the quota config for *tenant_id*, falling back to the default."""
        return self.tenant_quotas.get(tenant_id, self.default_quota)

    def permissions_for_agent(self, agent_id: str) -> AgentPermissions:
        """Return the permissions for *agent_id* (empty = unrestricted).

        Exact matches take priority over glob patterns.  When multiple
        patterns match, the first matching pattern (in config file order)
        is used.

        Examples of supported patterns::

            volundr-session-*   # matches volundr-session-abc, volundr-session-xyz
            ting                 # exact match
            claude-code         # exact match
        """
        # Exact match takes priority.
        if agent_id in self.agent_permissions:
            return self.agent_permissions[agent_id]

        # Glob pattern match (first match wins, preserving config order).
        for pattern, perms in self.agent_permissions.items():
            if fnmatch.fnmatch(agent_id, pattern):
                return perms

        return AgentPermissions()
