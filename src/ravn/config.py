"""Configuration settings for Ravn.

Config file locations (first found wins):
- ~/.ravn/config.yaml
- ./ravn.yaml
- /etc/ravn/config.yaml

Override with RAVN_CONFIG env var to point to a custom file.

Environment variable override format (RAVN_ prefix, double underscore for nesting):
- RAVN_ANTHROPIC__API_KEY
- RAVN_LLM__MODEL
- RAVN_MEMORY__BACKEND

Precedence (highest to lowest):
  env vars > yaml file > defaults

Note: project context files (.ravn.yaml, RAVN.md, CLAUDE.md) discovered by
`ravn.context.discover()` are a *separate* mechanism — they enrich the agent's
system prompt with project-specific instructions and are not config overrides.

ProjectConfig is the structured config overlay parsed from RAVN.md.  It lets
a project define allowed/forbidden tools, a persona, and an iteration budget
without modifying the global ravn.yaml.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from bifrost.config import PricingOverride
from mimir.client_config import MimirConfig
from niuu.domain.observability import ObservabilityConfig
from niuu.domain.tenancy import DEFAULT_PLATFORM_TENANT_ID
from niuu.mesh.config import DEFAULT_RPC_REPLY_CACHE_SIZE, MeshNatsConfig
from ravn.domain.permission_mode import (
    PermissionMode,
    parse_optional_permission_mode,
    parse_permission_mode,
)

# ---------------------------------------------------------------------------
# Config file resolution
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATHS: tuple[Path, ...] = (
    Path.home() / ".ravn" / "config.yaml",
    Path("./ravn.yaml"),
    Path("/etc/ravn/config.yaml"),
)


def _config_paths() -> tuple[Path, ...]:
    env = os.environ.get("RAVN_CONFIG")
    if env:
        return (Path(env),)
    return _DEFAULT_CONFIG_PATHS


# ---------------------------------------------------------------------------
# Sub-config models
# ---------------------------------------------------------------------------


#: LLM retry settings that were replaced by the gateway-outage window. A config that
#: still sets one is refused (naming the replacement) rather than ignored: ignoring
#: it would run the model calls on a bound the operator did not choose.
_REMOVED_LLM_RETRY_KEYS: dict[str, str] = {
    "max_retries": (
        "model-call retries are now bounded by wall-clock time, not an attempt count: "
        "set llm.gateway_outage_window_seconds (default 120) to the longest gateway "
        "restart to survive, and drop max_retries"
    ),
    "stream_resume_window_seconds": (
        "it was renamed because the window now bounds every retry of a model call, not "
        "only a cut stream: use llm.gateway_outage_window_seconds"
    ),
    "stream_resume_max_backoff_seconds": (
        "it was renamed: use llm.gateway_outage_max_backoff_seconds"
    ),
}


def _refuse_removed_llm_retry_keys(where: str, values: object) -> None:
    if not isinstance(values, dict):
        return
    for key, replacement in _REMOVED_LLM_RETRY_KEYS.items():
        if key in values:
            raise ValueError(f"{where}{key} is no longer supported: {replacement}")


class LLMProviderConfig(BaseModel):
    """A single LLM provider entry in the fallback chain."""

    adapter: str = Field(
        default="ravn.adapters.llm.anthropic.AnthropicAdapter",
        description="Fully-qualified class path for the LLM adapter.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra kwargs forwarded to the adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Maps kwarg names to env var names for credential injection.",
    )

    @field_validator("kwargs")
    @classmethod
    def _refuse_removed_retry_kwargs(cls, value: dict[str, Any]) -> dict[str, Any]:
        _refuse_removed_llm_retry_keys("llm.provider.kwargs.", value)
        return value


class ExtendedThinkingConfig(BaseModel):
    """Extended thinking (extended reasoning budget) configuration.

    Extended thinking allocates a deliberate reasoning budget to hard problems
    before the model produces its response. Each LLM adapter maps the generic
    reasoning request to its provider protocol.
    """

    enabled: bool = Field(
        default=False,
        description="Allow extended thinking to be activated.",
    )
    budget_tokens: int = Field(
        default=8000,
        description="Token budget allocated for thinking per activation.",
    )
    auto_trigger: bool = Field(
        default=True,
        description="Activate reasoning on every model call.",
    )
    auto_trigger_on_retry: bool = Field(
        default=True,
        description="Automatically activate thinking after the first tool failure.",
    )


class LLMConfig(BaseModel):
    """LLM provider configuration: primary provider and optional fallback chain."""

    model: str = Field(default="claude-sonnet-4-6")
    max_tokens: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Answer cap for every agent turn. null (the default) sends no cap: the "
            "provider bounds the answer by the model's maximum output or the rest of "
            "its context window, and the request timeout bounds the time. Adapters "
            "whose protocol requires the parameter (Anthropic) read the model's "
            "maximum output from the provider's catalog. Set a number only to spend "
            "less than that; a turn that reaches it fails with OutputTruncatedError "
            "and counts on ravn.cap.hits."
        ),
    )
    retry_base_delay: float = Field(
        default=1.0,
        gt=0.0,
        description=(
            "Seconds before the first retry of a failed model call; doubles per attempt up "
            "to gateway_outage_max_backoff_seconds."
        ),
    )
    gateway_outage_window_seconds: float = Field(
        default=120.0,
        ge=0.0,
        description=(
            "How long one model call keeps being retried while the gateway or provider is "
            "down (refused or reset connection, 429/500/502/503/504, or a stream cut or "
            "ended without its terminator), before the response header or mid-answer. "
            "Bounded by how long a gateway rollout takes: the time the call may spend "
            "failing, counted from each failure until the gateway answers again (the first "
            "event of a re-sent stream), so a stream that then runs long does not use it "
            "up. A cut answer is discarded and the same request sent again, so no tool "
            "call runs twice. Deterministic failures (other 4xx, truncation, a malformed "
            "answer) are never retried. Exhausting it raises LLMError naming this knob and "
            "counts on ravn.cap.hits; each retry logs at warning and counts on ravn.retries. "
            "0 disables retrying: the first failure fails the call."
        ),
    )
    gateway_outage_max_backoff_seconds: float = Field(
        default=10.0,
        gt=0.0,
        description=("Ceiling for the delay between retries within gateway_outage_window_seconds."),
    )
    timeout: float = Field(default=120.0)
    provider: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
    extended_thinking: ExtendedThinkingConfig = Field(
        default_factory=ExtendedThinkingConfig,
        description="Extended thinking (deliberate reasoning budget) configuration.",
    )

    @model_validator(mode="before")
    @classmethod
    def _refuse_removed_retry_settings(cls, data: Any) -> Any:
        _refuse_removed_llm_retry_keys("llm.", data)
        return data


class ToolAdapterConfig(BaseModel):
    """A single custom tool adapter entry."""

    adapter: str = Field(description="Fully-qualified class path for the tool adapter.")
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class FileToolsConfig(BaseModel):
    """File operation tool limits and thresholds."""

    max_read_bytes: int = Field(
        default=1 * 1024 * 1024,
        description="Maximum file size allowed for read_file (bytes).",
    )
    max_write_bytes: int = Field(
        default=5 * 1024 * 1024,
        description="Maximum content size allowed for write_file / edit_file (bytes).",
    )
    binary_check_bytes: int = Field(
        default=8 * 1024,
        description="Number of bytes inspected for binary (NUL-byte) detection.",
    )


class DockerTerminalConfig(BaseModel):
    """Docker-specific settings for the docker terminal backend."""

    image: str = Field(
        default="python:3.11-slim",
        description="Docker image used for the sandboxed container.",
    )
    network: str = Field(
        default="none",
        description="Docker network mode: 'none' (isolated), 'bridge', or 'host'.",
    )
    mount_workspace: bool = Field(
        default=True,
        description="Mount the workspace directory read-write inside the container.",
    )
    extra_mounts: list[str] = Field(
        default_factory=list,
        description="Additional volume mounts in 'host:container' format.",
    )


class TerminalToolConfig(BaseModel):
    """Terminal tool configuration."""

    backend: str = Field(
        default="local",
        description="Terminal backend: 'local' (host shell) or 'docker' (sandboxed container).",
    )
    persistent_shell: bool = Field(
        default=True,
        description="Keep a single shell process alive across tool calls.",
    )
    shell: str = Field(
        default="/bin/bash",
        description="Shell executable used for command execution.",
    )
    timeout_seconds: float = Field(
        default=30.0,
        description="Seconds to wait for a command to complete before timing out.",
    )
    docker: DockerTerminalConfig = Field(
        default_factory=DockerTerminalConfig,
        description="Docker backend configuration (used when backend='docker').",
    )


class WebFetchConfig(BaseModel):
    """web_fetch tool configuration."""

    allow_private_addresses: bool = Field(
        default=False,
        description=(
            "Allow web_fetch to connect to hosts that resolve to private or reserved "
            "addresses. Keep disabled for untrusted workloads."
        ),
    )
    timeout: float = Field(
        default=30.0,
        description="HTTP request timeout in seconds.",
    )
    user_agent: str = Field(
        default="Ravn/1.0 (+https://github.com/niuulabs/volundr)",
        description="User-Agent header sent with web_fetch requests.",
    )
    content_budget: int = Field(
        default=20_000,
        description="Maximum characters of extracted text returned by web_fetch.",
    )


class WebSearchConfig(BaseModel):
    """web_search tool configuration."""

    provider: ToolAdapterConfig = Field(
        default_factory=lambda: ToolAdapterConfig(
            adapter="ravn.adapters.tools.web_search.DuckDuckGoLiteSearchProvider"
        ),
        description="Web search provider adapter configuration.",
    )
    num_results: int = Field(
        default=5,
        description="Default number of search results to return.",
    )


class WebToolsConfig(BaseModel):
    """Configuration for built-in web tools."""

    fetch: WebFetchConfig = Field(default_factory=WebFetchConfig)
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class KubernetesToolsConfig(BaseModel):
    """Connection settings for the read-only Kubernetes inspection tool."""

    enabled: bool = Field(default=False)
    in_cluster: bool = Field(default=True)
    kubeconfig_env: str = Field(default="KUBECONFIG")
    kubeconfig_path: str = Field(default="")
    max_log_lines: int = Field(default=120, ge=1)
    max_configmap_value_chars: int = Field(
        default=4000,
        ge=1,
        description="Characters of each ConfigMap value kubernetes_inspect returns.",
    )
    max_list_items: int = Field(
        default=100,
        ge=1,
        description="Most objects a kubernetes_inspect listing (pods) returns.",
    )


class BashToolConfig(BaseModel):
    """Bash tool configuration (non-persistent, validation-gated execution)."""

    mode: str = Field(
        default="workspace_write",
        description=(
            "Permission mode for the bash tool. Mirrors PermissionConfig.mode. "
            "Controls which commands are allowed, denied, or require approval."
        ),
    )
    timeout_seconds: float = Field(
        default=120.0,
        description="Seconds to wait for a bash command before timing out.",
    )
    max_output_bytes: int = Field(
        default=100 * 1024,
        description=(
            "Maximum output size in bytes returned to the caller. "
            "Output exceeding this limit is truncated with a notice."
        ),
    )
    workspace_root: str = Field(
        default="",
        description=(
            "Absolute path to the workspace root used as the working directory "
            "and for path boundary checks. Defaults to CWD when empty."
        ),
    )


class ToolGroupConfig(BaseModel):
    """Named tool group configuration — controls which tool groups are active."""

    include_groups: list[str] = Field(
        default=[
            "core",
            "extended",
            "skill",
            "platform",
            "cascade",
            "mimir",
            "workflow",
            "ravn",
        ],
        description=(
            "Tool groups to include. Built-in groups: core, extended, skill, platform, "
            "mimir, workflow, ravn. "
            "The 'cascade' group signals that cascade tools (wired via build_cascade_tools) "
            "should be appended when the profile is selected by the coordinator. "
            "The 'mimir' group enables mimir_* knowledge-base tools. "
            "The 'workflow' group enables workflow_* tools backed by capability_sources. "
            "The 'ravn' group enables persona, skill, and capability catalog tools."
        ),
    )
    include_mcp: bool = Field(
        default=True,
        description="Whether to append MCP tools when this profile is active.",
    )


class ToolsConfig(BaseModel):
    """Tool availability and custom adapter configuration."""

    max_result_chars: int = Field(
        default=100_000,
        description=(
            "Maximum characters of a single tool result injected into agent "
            "history. Oversized results are truncated with an explicit marker; "
            "context compression protects the most recent messages, so one "
            "giant result would otherwise make the turn unrecoverable "
            "(NIU-1118). 0 disables the cap."
        ),
    )
    enabled: list[str] = Field(
        default_factory=list,
        description=(
            "Allowlist of built-in tool names to enable. "
            "Empty list means all built-in tools are enabled."
        ),
    )
    disabled: list[str] = Field(
        default_factory=list,
        description="Blocklist of built-in tool names to disable.",
    )
    custom: list[ToolAdapterConfig] = Field(
        default_factory=list,
        description="Custom tool adapters to register alongside built-ins.",
    )
    profiles: dict[str, ToolGroupConfig] = Field(
        default_factory=dict,
        description=(
            "Named tool profiles. Built-in profiles: 'default' (full set) and "
            "'worker' (core only, no MCP). Custom profiles override built-in defaults."
        ),
    )
    file: FileToolsConfig = Field(
        default_factory=FileToolsConfig,
        description="Limits and thresholds for the built-in file tools.",
    )
    terminal: TerminalToolConfig = Field(
        default_factory=TerminalToolConfig,
        description="Persistent shell configuration for the built-in terminal tool.",
    )
    web: WebToolsConfig = Field(
        default_factory=WebToolsConfig,
        description="Configuration for the built-in web tools (web_fetch, web_search).",
    )
    kubernetes: KubernetesToolsConfig = Field(
        default_factory=KubernetesToolsConfig,
        description="Connection settings for the built-in Kubernetes inspection tool.",
    )
    bash: BashToolConfig = Field(
        default_factory=BashToolConfig,
        description="Configuration for the bash tool (non-persistent, validation-gated).",
    )


class RerankerConfig(BaseModel):
    """Second-stage ranking for retrieved memories.

    Off by default. Retrieval already over-fetches, so this only decides which candidates survive
    the cut — an embedding is computed once without seeing the question, while a reranker judges
    query and document together. A failure is never fatal: the retrieval order stands.
    """

    enabled: bool = Field(
        default=False,
        description="Rerank retrieved episodes before admitting them to the prompt.",
    )
    adapter: str = Field(
        default="niuu.adapters.reranker.qwen.QwenRerankerAdapter",
        description="Fully-qualified class path for the reranker adapter.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra kwargs forwarded to the reranker adapter constructor.",
    )


class EmbeddingConfig(BaseModel):
    """Embedding backend configuration for semantic memory search."""

    enabled: bool = Field(
        default=False,
        description="Enable embedding-based semantic search in episodic memory.",
    )
    adapter: str = Field(
        default="niuu.adapters.embedding.sentence_transformer.SentenceTransformerEmbeddingAdapter",
        description="Fully-qualified class path for the embedding adapter.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra kwargs forwarded to the embedding adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Maps kwarg names to env var names for credential injection.",
    )
    rrf_k: int = Field(
        default=60,
        description="Reciprocal Rank Fusion constant k (higher = less top-rank bias).",
    )
    semantic_candidate_limit: int = Field(
        default=50,
        description="Maximum number of episodes scanned for cosine similarity.",
    )


class SkillConfig(BaseModel):
    """Skill extraction and discovery configuration."""

    enabled: bool = Field(
        default=True,
        description="Enable automatic skill extraction from recurring episode patterns.",
    )
    backend: Literal["file", "sqlite"] = Field(
        default="file",
        description="Skill storage backend: 'file' (Markdown registry) or 'sqlite'.",
    )
    path: str = Field(
        default="~/.ravn/skills.db",
        description="SQLite database path for skill storage.",
    )
    suggestion_threshold: int = Field(
        default=3,
        description="Minimum matching SUCCESS episodes before a skill is synthesised.",
    )
    cache_max_entries: int = Field(
        default=128,
        description="Maximum entries in the in-process LRU skill cache.",
    )
    skill_dirs: list[str] = Field(
        default_factory=list,
        description=(
            "Extra directories to search for user-defined skill Markdown files, "
            "in addition to the default .ravn/skills/ and ~/.ravn/skills/ paths. "
            "Paths are searched in order; earlier entries have higher priority."
        ),
    )
    include_builtin: bool = Field(
        default=True,
        description="Include the built-in skills shipped with Ravn in the skill registry.",
    )


class MemoryConfig(BaseModel):
    """Conversation memory / persistence backend configuration."""

    backend: Literal["none", "sqlite", "postgres"] | str = Field(
        default="sqlite",
        description=(
            "Backend to use: 'none', 'sqlite', 'postgres', or a fully-qualified "
            "class path for a custom backend adapter."
        ),
    )
    path: str = Field(
        default="~/.ravn/memory.db",
        description="File path for sqlite backend.",
    )
    dsn: str = Field(
        default="",
        description="PostgreSQL DSN for postgres backend.",
    )
    dsn_env: str = Field(
        default="",
        description="Env var name to read the DSN from (takes precedence over dsn).",
    )
    prefetch_budget: int = Field(
        default=2000,
        description="Maximum approximate tokens of past context injected per turn.",
    )
    prefetch_limit: int = Field(
        default=5,
        ge=0,
        description="Maximum number of episodes retrieved during prefetch; 0 disables it.",
    )
    prefetch_min_relevance: float = Field(
        default=0.3,
        description="Minimum relevance score (0–1) for an episode to appear in prefetch.",
    )
    recency_half_life_days: float = Field(
        default=14.0,
        description="Half-life in days for the exponential recency decay applied to episodes.",
    )
    recency_floor: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum recency multiplier, so an old-but-relevant episode still ranks. "
            "Recency multiplies into the combined score, so without a floor the decay "
            "acts as an age filter rather than a ranking signal: at the default "
            "half-life nothing older than ~24 days can clear prefetch_min_relevance. "
            "0.0 restores the previous unbounded decay."
        ),
    )
    corpus_stats_interval_seconds: float = Field(
        default=300.0,
        description=(
            "Minimum seconds between corpus-health gauge samples (episode count, "
            "embedding and search-index coverage). 0 disables sampling."
        ),
    )
    max_retries: int = Field(
        default=15,
        description="Maximum retry attempts on SQLite 'database is locked' errors.",
    )
    min_retry_jitter_ms: float = Field(
        default=20.0,
        description="Minimum random jitter (ms) between SQLite retry attempts.",
    )
    max_retry_jitter_ms: float = Field(
        default=150.0,
        description="Maximum random jitter (ms) between SQLite retry attempts.",
    )
    checkpoint_interval: int = Field(
        default=50,
        description="Number of writes between passive WAL checkpoints.",
    )
    session_search_truncate_chars: int = Field(
        default=100_000,
        description="Maximum characters of episode content returned per session in session_search.",
    )
    # Reflection config (merged from OutcomeConfig, NIU-574)
    reflection_model: str = Field(
        default="claude-haiku-4-5-20251001",
        description=(
            "Model alias used for the compact post-task reflection call. Use empty, "
            "'default', 'agent', or 'same-as-agent' to reuse the effective agent model; "
            "use 'off', 'disabled', or 'none' to retain raw episodes without generated "
            "reflection."
        ),
    )
    reflection_max_tokens: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Answer cap for the post-task episode reflection and the ravn_reflect tool. "
            "null (the default) sends no cap, so the model's own output limit (or the "
            "rest of its context window) applies; a reasoning model spends part of any "
            "cap thinking. A cut-off answer raises and counts on ravn.cap.hits."
        ),
    )
    task_summary_max_chars: int = Field(
        default=200,
        description="Maximum characters of the user input stored as the task description.",
    )
    input_token_cost_per_million: float = Field(
        default=3.0,
        description="Input token cost in USD per million tokens (used to estimate cost_usd).",
    )
    output_token_cost_per_million: float = Field(
        default=15.0,
        description="Output token cost in USD per million tokens (used to estimate cost_usd).",
    )
    rolling_summary_max_chars: int = Field(
        default=2_000,
        description=(
            "Maximum characters kept in the in-memory rolling session summary "
            "maintained by MemoryPort.on_turn_complete()."
        ),
    )


class PermissionRuleConfig(BaseModel):
    """A single permission rule entry."""

    pattern: str = Field(description="Permission name or glob pattern.")
    action: Literal["allow", "deny", "ask"] = Field(
        default="ask",
        description="Action to take: 'allow', 'deny', or 'ask'.",
    )


class PermissionConfig(BaseModel):
    """Permission enforcement configuration."""

    # Assignment is validated too, so ``permission.mode = "read-only"`` parses
    # into the enum instead of storing a string no enforcer branch matches.
    model_config = ConfigDict(validate_assignment=True)

    mode: PermissionMode = Field(
        default=PermissionMode.WORKSPACE_WRITE,
        description=(
            "Permission mode: "
            "'read_only' (no mutations), "
            "'workspace_write' (writes within workspace only), "
            "'full_access' (unrestricted, explicit opt-in), "
            "'prompt' (interactive confirmation per action). "
            "Hyphenated spellings ('read-only') are accepted; "
            "legacy 'allow_all' / 'deny_all' remain supported."
        ),
    )
    workspace_root: str = Field(
        default="",
        description=(
            "Absolute path to the workspace root enforced in workspace_write mode. "
            "Defaults to the current working directory when empty."
        ),
    )
    allow: list[str] = Field(
        default_factory=list,
        description="Tool names or permission strings always granted without prompting.",
    )
    deny: list[str] = Field(
        default_factory=list,
        description="Tool names or permission strings always denied.",
    )
    ask: list[str] = Field(
        default_factory=list,
        description="Tool names or permission strings that always prompt the user.",
    )
    rules: list[PermissionRuleConfig] = Field(
        default_factory=list,
        description="Ordered rules evaluated before the default mode.",
    )

    @field_validator("mode", mode="before")
    @classmethod
    def _parse_mode(cls, value: object) -> PermissionMode:
        return parse_permission_mode(value)


class MCPAuthConfig(BaseModel):
    """Authentication configuration for a single MCP server.

    auth_type determines which flow is used when ``mcp_auth`` is called.
    All sensitive values (secrets, API keys) must be referenced via env-var
    names rather than inlined — Bifrost or the RAVN.md secrets block injects
    the actual values at runtime.
    """

    auth_type: str | None = Field(
        default=None,
        description=(
            "Auth flow: 'api_key', 'device_flow', or 'client_credentials'. "
            "None means no auth required."
        ),
    )
    # OAuth 2.0 (device_flow + client_credentials)
    token_url: str = Field(default="", description="OAuth token endpoint URL.")
    client_id: str = Field(default="", description="OAuth client ID.")
    client_secret_env: str = Field(
        default="",
        description="Environment variable name holding the OAuth client secret.",
    )
    scope: str = Field(default="", description="Space-separated OAuth scopes.")
    audience: str = Field(default="", description="OAuth audience claim (optional).")
    # API key
    api_key_env: str = Field(
        default="",
        description="Environment variable name holding the API key value.",
    )
    api_key_header: str = Field(
        default="Authorization",
        description="HTTP header used to send the API key.",
    )
    api_key_prefix: str = Field(
        default="Bearer",
        description="Value prefix, e.g. 'Bearer' or 'ApiKey'.",
    )


class MCPTokenStoreConfig(BaseModel):
    """Configuration for the MCP token persistence backend.

    'local' (Pi mode): tokens stored in an encrypted JSON file.
    'openbao' (infra mode): tokens stored in an OpenBao KV v2 secret.
    """

    backend: Literal["local", "openbao"] = Field(
        default="local",
        description="Token store backend: 'local' (encrypted file) or 'openbao'.",
    )
    local_path: str = Field(
        default="~/.ravn/mcp_tokens.json",
        description="Path for the local encrypted token file.",
    )
    openbao_url: str = Field(
        default="http://openbao:8200",
        description="OpenBao base URL.",
    )
    openbao_token_env: str = Field(
        default="OPENBAO_TOKEN",
        description="Environment variable name holding the OpenBao token.",
    )
    openbao_mount: str = Field(
        default="secret",
        description="OpenBao KV secrets engine mount path.",
    )
    openbao_path_prefix: str = Field(
        default="ravn/mcp",
        description="Sub-path prefix within the KV mount.",
    )


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    name: str = Field(description="Human-readable name for this server.")
    transport: Literal["stdio", "sse", "http"] = Field(
        default="stdio",
        description="Transport type: 'stdio', 'sse', or 'http'.",
    )
    command: str = Field(
        default="",
        description="Command to launch the server (stdio transport).",
    )
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Extra environment variables passed to the server process.",
    )
    url: str = Field(
        default="",
        description="URL for http/sse transport.",
    )
    timeout: float = Field(
        default=30.0,
        description="Request/read timeout in seconds.",
    )
    connect_timeout: float = Field(
        default=10.0,
        description="Connection timeout in seconds (SSE transport).",
    )
    enabled: bool = Field(default=True)
    auth: MCPAuthConfig = Field(
        default_factory=MCPAuthConfig,
        description="Authentication configuration for this server.",
    )


class HookConfig(BaseModel):
    """Configuration for a single pre/post tool hook."""

    adapter: str = Field(description="Fully-qualified class path for the hook.")
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)
    events: list[str] = Field(
        default_factory=lambda: ["pre_tool", "post_tool"],
        description="Events this hook fires on: 'pre_tool', 'post_tool'.",
    )


class HooksConfig(BaseModel):
    """Pre/post tool hook configuration."""

    pre_tool: list[HookConfig] = Field(default_factory=list)
    post_tool: list[HookConfig] = Field(default_factory=list)


class ChannelConfig(BaseModel):
    """Configuration for a single output channel."""

    adapter: str = Field(
        default="ravn.adapters.cli_channel.CliChannel",
        description="Fully-qualified class path for the channel adapter.",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class ContextConfig(BaseModel):
    """Project context discovery configuration."""

    per_file_limit: int = Field(
        default=4096,
        description="Maximum characters read from a single context file.",
    )
    total_budget: int = Field(
        default=12288,
        description="Maximum total characters of context injected into the system prompt.",
    )


class AgentConfig(BaseModel):
    """Core agent behaviour configuration."""

    model: str = Field(default="claude-sonnet-4-6")
    max_tokens: int | None = Field(
        default=None, gt=0, description="Deprecated: use llm.max_tokens."
    )
    max_iterations: int = Field(
        default=20,
        description="Max tool-call iterations per turn. Set 0 for no cap.",
    )
    system_prompt: str = Field(
        default=(
            "You are Ravn, a helpful AI assistant. "
            "Be concise, accurate, and use tools when they help."
        )
    )
    episode_summary_max_chars: int = Field(
        default=500,
        description="Maximum characters of the agent response stored as an episode summary.",
    )
    episode_task_max_chars: int = Field(
        default=200,
        description="Maximum characters of the user input stored as the episode task description.",
    )


# ---------------------------------------------------------------------------
# Context management config (NIU-431)
# ---------------------------------------------------------------------------


class IterationBudgetConfig(BaseModel):
    """Iteration budget configuration."""

    total: int = Field(
        default=90,
        description="Total iterations allowed across a session or cascade.",
    )
    near_limit_threshold: float = Field(
        default=0.8,
        description=(
            "Fraction of total iterations consumed before 'near limit' warnings are emitted "
            "(0.0–1.0, default 0.8 = 80%)."
        ),
    )


class CompactionMode(StrEnum):
    """Who compacts a model's context when it grows past the budget."""

    RAVN = "ravn"
    """Ravn's own ContextCompressor summarises older history client-side."""
    NATIVE = "native"
    """The provider compacts (OpenAI Responses / Anthropic compaction); Ravn never does."""
    OFF = "off"
    """Nobody compacts; the prompt budget check is the only guard."""


class ContextManagementConfig(BaseModel):
    """Context compression and prompt-builder configuration."""

    compaction: CompactionMode = Field(
        default=CompactionMode.RAVN,
        description=(
            "Who compacts the context of models not listed in compaction_models: "
            "'ravn' (Ravn's client-side compactor, the default), 'native' (the "
            "provider's own compaction — the LLM adapter must support it, or startup "
            "fails) or 'off'. Never both: with 'native' Ravn does not compact."
        ),
    )
    compaction_models: dict[str, CompactionMode] = Field(
        default_factory=dict,
        description=(
            "Per-model override of 'compaction', keyed by model id, e.g. "
            "{'gpt-6-sol': 'native', 'Qwen/Qwen3.8-27B': 'ravn'}."
        ),
    )

    context_window_tokens: int = Field(
        default=0,
        ge=0,
        description=(
            "Model context-window size in tokens: the real bound on a prompt. The prompt "
            "ceiling is this window minus llm.max_tokens when an answer cap is set, else "
            "the whole window (an answer that then does not fit comes back cut off and "
            "raises OutputTruncatedError). 0 means the window is unknown."
        ),
    )
    token_estimate_safety_factor: float = Field(
        default=1.5,
        ge=1.0,
        description=(
            "Multiplier applied to serialized prompt-size estimates before compression "
            "and budget enforcement. It covers tokenizer and chat-template overhead "
            "when the configured provider does not expose preflight token counts."
        ),
    )
    compression_threshold: float = Field(
        default=0.8,
        description=(
            "Fraction of the effective safe prompt budget that triggers compaction "
            "(0.0–1.0, default 0.8)."
        ),
    )
    protect_first_messages: int = Field(
        default=2,
        description="Number of messages at the start of history to preserve unchanged.",
    )
    protect_last_messages: int = Field(
        default=6,
        description="Number of messages at the end of history to preserve unchanged.",
    )
    compact_recent_turns: int = Field(
        default=3,
        description=(
            "Number of recent conversation turns (user+assistant pairs) to preserve "
            "verbatim at the end of the history.  Overrides protect_last_messages when "
            "non-zero: protect_last = compact_recent_turns * 2."
        ),
    )
    compression_max_tokens: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Answer cap for the compaction state document. A cut-off document never "
            "replaces history: compaction raises ContextCompressionError. "
            "null (the default) sends no cap, so the model's own output limit (or the "
            "rest of its context window) applies; a reasoning model spends part of any "
            "cap thinking. A cut-off answer raises and counts on ravn.cap.hits."
        ),
    )
    prompt_cache_max_entries: int = Field(
        default=16,
        description="Maximum number of entries in the in-process LRU prompt cache.",
    )
    prompt_cache_dir: str = Field(
        default="~/.ravn/prompt_cache",
        description="Directory for disk-snapshot prompt cache entries.",
    )
    max_prompt_tokens: int = Field(
        default=0,
        ge=0,
        description=(
            "Hard per-call ceiling for the estimated prompt size (system prompt + "
            "tool schemas + message history), in tokens. 0 derives it from "
            "context_window_tokens (and no check when that is unknown too). A prompt "
            "still above it after compaction is refused with PromptBudgetExceededError, "
            "and a single tool result is cut only where it alone would break it. Set it "
            "below the window only for a real limit; for prompt-size latency use "
            "compaction_target_tokens, which compacts history instead of cutting results."
        ),
    )
    compaction_target_tokens: int = Field(
        default=0,
        ge=0,
        description=(
            "Prompt size compaction keeps history under, for prefill latency: compaction "
            "fires at compression_threshold x this. 0 uses the prompt ceiling. This "
            "never cuts a tool result or refuses a turn; on self-hosted models a smaller "
            "prompt mainly means a faster time to first token."
        ),
    )

    def compaction_for(self, model: str) -> CompactionMode:
        """The compaction mode configured for *model*."""
        return self.compaction_models.get(model, self.compaction)

    def effective_protect_last(self) -> int:
        """Return the protect_last value to pass to ContextCompressor.

        When ``compact_recent_turns`` is non-zero it takes precedence:
        ``protect_last = compact_recent_turns * 2`` (one turn = user + assistant).
        Falls back to ``protect_last_messages`` when ``compact_recent_turns`` is 0.
        """
        if self.compact_recent_turns > 0:
            return self.compact_recent_turns * 2
        return self.protect_last_messages


# ---------------------------------------------------------------------------
# Legacy adapter config (kept for backwards compat with NIU-426 wiring)
# ---------------------------------------------------------------------------


class TelegramChannelConfig(BaseModel):
    """Telegram channel configuration."""

    enabled: bool = Field(default=False)
    token_env: str = Field(
        default="TELEGRAM_BOT_TOKEN",
        description="Environment variable name containing the Telegram bot token.",
    )
    allowed_chat_ids: list[int] = Field(
        default_factory=list,
        description="Chat IDs allowed to interact with the bot. Empty list means all.",
    )
    poll_timeout: int = Field(
        default=30,
        description="Long-poll timeout in seconds for getUpdates.",
    )
    retry_delay: float = Field(
        default=5.0,
        description="Seconds to wait after a poll error before retrying.",
    )
    message_max_chars: int = Field(
        default=4096,
        description="Characters per outbound Telegram message (API limit); longer is split.",
    )


class HttpChannelConfig(BaseModel):
    """Local HTTP gateway channel configuration."""

    enabled: bool = Field(default=False)
    host: str = Field(
        default="127.0.0.1",
        description="Host/IP to bind the HTTP gateway server.",
    )
    port: int = Field(
        default=7477,
        description="TCP port for the HTTP gateway server.",
    )
    translator: str = Field(
        default="ravn.adapters.events.cli_translator.CliFormatTranslator",
        description="Fully-qualified class path for the EventTranslatorPort implementation.",
    )


class SkuldChannelConfig(BaseModel):
    """Skuld WebSocket channel configuration for browser delivery.

    Used by both gateway mode and daemon/mesh mode to deliver events
    to the Skuld broker for browser visualization.
    """

    enabled: bool = Field(default=False)
    broker_url: str = Field(
        default="ws://localhost:8081/ws/ravn",
        description="WebSocket URL of the Skuld broker endpoint. "
        "The peer_id is appended automatically (e.g. ws://localhost:8081/ws/ravn/{peer_id}).",
    )
    display_name: str = Field(
        default="",
        description="Human-friendly name shown in the mesh UI (e.g. 'Kvasir'). "
        "Falls back to the persona name when empty.",
    )
    reconnect_delay_seconds: float = Field(
        default=2.0,
        gt=0,
        description="Delay between reconnect attempts to the Skuld broker.",
    )
    max_reconnect_attempts: int = Field(
        default=5,
        ge=1,
        description="Reconnect attempts before giving up. Also used for the "
        "cross-session joins the session_join tool opens.",
    )
    session_ready_timeout_seconds: float = Field(
        default=600.0,
        gt=0,
        description=(
            "Maximum time to wait for a launched Volundr session to reach "
            "running before opening a cross-session join."
        ),
    )


class PlatformWorkflowAliasConfig(BaseModel):
    """Named workflow launch target for resident platform tools."""

    workflow_id: str = Field(
        default="",
        description="Concrete Ting workflow id. Preferred when known.",
    )
    name: str = Field(
        default="",
        description="Exact workflow name to resolve when workflow_id is not configured.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Tags that must be present on the resolved workflow.",
    )
    scope: str = Field(
        default="all",
        description="Workflow scope to query while resolving by name or tags.",
    )
    defaults: dict[str, Any] = Field(
        default_factory=dict,
        description="Default launch fields merged before explicit tool input.",
    )


class PlatformToolsConfig(BaseModel):
    """Platform integration tools (Volundr sessions, git, Ting sagas, tracker)."""

    enabled: bool = Field(
        default=False,
        description="Register platform tools (requires Volundr/Ting backend).",
    )
    base_url: str = Field(default="http://localhost:8080")
    timeout: float = Field(default=30.0)
    access_token: str = Field(
        default="",
        description=(
            "Explicit bearer token for platform API authentication: a PAT for an "
            "off-cluster resident, or the workload-bound token a sandbox runtime "
            "mediates. Leave empty in-cluster to use projected workload identity."
        ),
    )
    anonymous_dev_mode: bool = Field(
        default=False,
        description=(
            "Explicitly use an unauthenticated platform HTTP client for local development. "
            "Production callers should leave this disabled and use a PAT or workload identity."
        ),
    )
    workload_token_file: str = Field(
        default="/var/run/secrets/kubernetes.io/serviceaccount/token",
        description="Projected workload identity token file used when access_token is not set.",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_renamed_pat_token(cls, data: Any) -> Any:
        """``pat_token`` was renamed; dropping it silently would drop the credential."""
        if isinstance(data, dict) and "pat_token" in data:
            raise ValueError(
                "gateway.platform.pat_token was renamed to gateway.platform.access_token "
                "(env RAVN_GATEWAY__PLATFORM__ACCESS_TOKEN): it carries any platform "
                "bearer, not only a PAT. Rename the key in this config."
            )
        return data

    workload_exchange_url: str = Field(
        default="",
        description=(
            "Optional workload token exchange URL. Defaults to "
            "base_url /api/v1/tokens/workload/exchange."
        ),
    )
    workload_audiences: list[str] = Field(
        default_factory=lambda: ["volundr-api", "forge", "ting", "mimir", "guild"],
        description="Target service audiences requested from workload token exchange.",
    )
    a2a_trusted_origins: list[str] = Field(
        default_factory=list,
        description=(
            "Additional peer Agent Card origins allowed to receive platform A2A "
            "credentials. The platform base URL is always trusted."
        ),
    )
    a2a_agent_card_urls: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit trusted Agent Card URLs to include beside Guild-discovered peers. "
            "Use this for platform workflow facades or other stable peers that are not "
            "projected by an Observatory Agent Directory."
        ),
    )
    a2a_default_connection_id: str = Field(
        default="",
        description="Default Volundr target injected into newly started A2A workflows.",
    )
    a2a_default_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Default A2A launch metadata; explicit per-call metadata takes precedence.",
    )
    a2a_gate_decisions: bool = Field(
        default=True,
        description=(
            "Whether this Ravn may answer workflow gates over A2A. Turned off for agents whose "
            "gates belong to a person, so the refusal does not rest on the model's judgment."
        ),
    )
    a2a_message_max_chars: int = Field(
        default=12_000,
        ge=1_000,
        description="Maximum A2A prompt, answer, or metadata payload size.",
    )
    a2a_result_max_chars: int = Field(
        default=12_000,
        ge=1_000,
        description="Maximum model-facing A2A task snapshot size.",
    )
    workflow_aliases: dict[str, PlatformWorkflowAliasConfig] = Field(
        default_factory=dict,
        description=(
            "Resident-local aliases for Ting workflow launches. "
            "Example: research -> workflow id/name plus launch defaults."
        ),
    )


class DiscordChannelConfig(BaseModel):
    """Discord bot gateway configuration."""

    enabled: bool = Field(default=False)
    token_env: str = Field(
        default="DISCORD_BOT_TOKEN",
        description="Environment variable name containing the Discord bot token.",
    )
    guild_id: str = Field(
        default="",
        description="Default guild (server) ID; used when chat_id has no guild prefix.",
    )
    command_prefix: str = Field(
        default="/",
        description="Prefix character for slash commands recognised by the bot.",
    )
    message_max_chars: int = Field(
        default=2000,
        description="Characters per outbound Discord message (API limit); longer is split.",
    )
    retry_delay: float = Field(
        default=5.0,
        description="Seconds to wait after a gateway error before reconnecting.",
    )
    gateway_url: str = Field(
        default="wss://gateway.discord.gg/?v=10&encoding=json",
        description="Discord Gateway WebSocket URL.",
    )
    api_base: str = Field(
        default="https://discord.com/api/v10",
        description="Discord REST API base URL.",
    )
    max_pending_approvals: int = Field(
        default=1000,
        description="Maximum number of approval-requested messages tracked in memory.",
    )


class SlackChannelConfig(BaseModel):
    """Slack bot gateway configuration."""

    enabled: bool = Field(default=False)
    bot_token_env: str = Field(
        default="SLACK_BOT_TOKEN",
        description="Environment variable name containing the Slack bot token (xoxb-).",
    )
    app_token_env: str = Field(
        default="SLACK_APP_TOKEN",
        description="Environment variable name for Socket Mode app token (xapp-).",
    )
    poll_interval: float = Field(
        default=2.0,
        description="Seconds between conversations.history polls (fallback polling mode).",
    )
    message_max_chars: int = Field(
        default=3000,
        description="Maximum characters per outbound Slack message; longer text is split.",
    )
    retry_delay: float = Field(
        default=5.0,
        description="Seconds to wait after an API error before retrying.",
    )
    api_base: str = Field(
        default="https://slack.com/api",
        description="Slack Web API base URL.",
    )


class MatrixChannelConfig(BaseModel):
    """Matrix client-server gateway configuration."""

    enabled: bool = Field(default=False)
    homeserver: str = Field(
        default="https://matrix.niuu.world",
        description="Matrix homeserver base URL.",
    )
    user_id_env: str = Field(
        default="MATRIX_USER_ID",
        description="Environment variable name containing the Matrix user ID (@user:server).",
    )
    access_token_env: str = Field(
        default="MATRIX_ACCESS_TOKEN",
        description="Environment variable name containing the Matrix access token.",
    )
    e2e: Literal[False] = Field(
        default=False,
        description="Reserved for Matrix encryption support; true is rejected.",
    )
    sync_timeout_ms: int = Field(
        default=30000,
        description="Long-poll timeout in milliseconds for the /sync endpoint.",
    )
    retry_delay: float = Field(
        default=5.0,
        description="Seconds to wait after a sync error before retrying.",
    )
    message_max_chars: int = Field(
        default=32000,
        description="Maximum characters per outbound Matrix message; longer text is split.",
    )


class WhatsAppChannelConfig(BaseModel):
    """WhatsApp gateway configuration (Meta Cloud API)."""

    enabled: bool = Field(default=False)
    mode: Literal["business_api"] = Field(
        default="business_api",
        description="Adapter mode. Only the Meta Cloud business API is supported.",
    )
    api_key_env: str = Field(
        default="WA_API_KEY",
        description="Environment variable name containing the Meta API key (bearer token).",
    )
    phone_number_id_env: str = Field(
        default="WA_PHONE_NUMBER_ID",
        description="Environment variable name containing the WhatsApp phone number ID.",
    )
    webhook_verify_token_env: str = Field(
        default="WA_WEBHOOK_VERIFY_TOKEN",
        description="Environment variable name for Meta webhook verification token.",
    )
    webhook_secret_env: str = Field(
        default="WA_WEBHOOK_SECRET",
        description=(
            "Environment variable name for Meta webhook HMAC secret "
            "(X-Hub-Signature-256). Leave unset to skip signature verification."
        ),
    )
    webhook_host: str = Field(
        default="0.0.0.0",
        description="Host to bind the inbound webhook HTTP listener.",
    )
    webhook_port: int = Field(
        default=7478,
        description="Port for the inbound webhook HTTP listener.",
    )
    retry_delay: float = Field(
        default=5.0,
        description="Seconds to wait after an API error before retrying.",
    )
    message_max_chars: int = Field(
        default=4096,
        description="Maximum characters per outbound WhatsApp message; longer text is split.",
    )
    api_base: str = Field(
        default="https://graph.facebook.com/v18.0",
        description="Meta Graph API base URL.",
    )


class OpenClawChannelConfig(BaseModel):
    """OpenClaw-protocol gateway channel — makes Ravn look like an OpenClaw
    gateway to the LexiChat iOS app.

    Serves a WebSocket at the ROOT path plus ``GET /health``, speaking the
    req/res/event protocol the shipped app's OpenClawKit already implements, so
    Ravn conversations appear in the same channel list as OpenClaw ones with no
    client change. See ``ravn.adapters.channels.gateway_openclaw``.

    Bind to the tailnet address, never publicly: the iOS client hardcodes
    ``ws://`` and ``http://`` and cannot speak TLS, so the tailnet *is* the
    transport security.
    """

    enabled: bool = Field(default=False)
    host: str = Field(
        default="127.0.0.1",
        description="Host/IP to bind the OpenClaw-protocol server.",
    )
    port: int = Field(
        default=18790,
        description=(
            "TCP port for the OpenClaw-protocol server. Deliberately NOT 18789 "
            "— that is the real OpenClaw gateway's port."
        ),
    )
    path: str = Field(
        default="/",
        description=(
            "URL path of the WebSocket; GET <path>/health answers beside it. The app "
            "connects at the root. Behind an ingress the resident shares with other "
            "routes, give the shim its own prefix (e.g. /openclaw)."
        ),
    )
    token_env: str = Field(
        default="RAVN_OPENCLAW_TOKEN",
        description=(
            "Environment variable holding the bearer token clients must present. "
            "Use a value distinct from every real OpenClaw gateway token so a "
            "leak is scoped. An unset token refuses every connect."
        ),
    )
    agent_id: str = Field(
        default="travis",
        description=(
            "Agent id advertised to the client. Becomes segment 1 of every "
            "session key (``agent:<id>:main``) and the app's agent lens."
        ),
    )
    session_prefix: str = Field(
        default="",
        description=(
            "Allowlist prefix for addressable session keys. Defaults to "
            "``agent:<agent_id>:``. Only keys matching it may be read or sent "
            "to — without this a client could pass a Telegram session key and "
            "inject a turn into a real thread, since Ravn performs no ownership "
            "check of its own."
        ),
    )
    max_live_sessions: int = Field(
        default=32,
        description=(
            "Cap on sessions this channel will mint. RavnGateway._sessions is "
            "never evicted, so an uncapped client could grow it without bound."
        ),
    )
    store_path: str = Field(
        default="~/.ravn/openclaw/state.db",
        description=(
            "SQLite transcript. This store, not Ravn and not the phone, is the "
            "system of record — Ravn keeps sessions in RAM only and exposes no "
            "history endpoint, so chat.history has nothing else to read."
        ),
    )
    notifications_enabled: bool = Field(
        default=True,
        description=(
            "Serve the resident's notification outbox (what it waits on its owner for, "
            "and what its agent notifies on purpose) to Forge over this shim, and offer "
            "the agent the `notify` tool. The outbox shares store_path's database."
        ),
    )
    notifications_max_pending: int = Field(
        default=1000,
        ge=1,
        description=(
            "Most outbox entries kept while Forge has not acknowledged them. Past it the "
            "oldest are dropped, with a warning: Forge has not reached this shim for long."
        ),
    )

    @field_validator("path")
    @classmethod
    def _path_is_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(f"gateway.channels.openclaw.path {value!r} must start with '/'")
        return value


class VoiceMinterConfig(BaseModel):
    """One realtime provider's credential minter (dynamic adapter + kwargs)."""

    adapter: str = Field(
        description=(
            "Fully-qualified class path implementing RealtimeCredentialMinterPort, e.g. "
            "'ravn.adapters.voice.client_secrets.XaiRealtimeMinter'."
        ),
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Constructor keyword arguments (key_file, base_url, ttl_seconds, …).",
    )


class VoiceCallPushConfig(BaseModel):
    """How the resident rings the phone by VoIP push (dynamic adapter + kwargs)."""

    adapter: str = Field(
        description=(
            "Fully-qualified class path implementing CallPushPort, e.g. "
            "'ravn.adapters.voice.call_push.ForgeCallPushClient'."
        ),
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict, description="Constructor keyword arguments (base_url, …)."
    )


class VoiceCallsConfig(BaseModel):
    """Calls the resident places itself: ``notify`` with ``speak`` rings the owner's phone.

    The gateway decides once per request and sends ``call.request`` over the phone's
    standby socket; the phone still applies its own allow-list, Focus and quiet hours,
    and answers ``call.result``. There is no rate cap or gap between calls (Jozef,
    2026-11-03: Focus and quiet hours only).
    """

    enabled: bool = Field(
        default=False,
        description="Offer speak on notify and place calls. Off: notify has no speak.",
    )
    resident_id: str = Field(
        default="",
        description=(
            "This resident's id (its Forge runtime id): the call.request's session.id, which "
            "the phone matches against the resident it selected for voice, and the anchor of "
            "the notification id Forge derives. Required when enabled."
        ),
    )
    host: str = Field(
        default="",
        description="The cluster the resident runs in, as the phone names it (session.host).",
    )
    expires_after_seconds: int = Field(
        default=300,
        ge=30,
        description="A request not delivered by then is dropped (its expiresAt).",
    )
    result_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        description="How long a socket delivery waits for call.result before ringing by push once.",
    )
    poll_interval_seconds: float = Field(
        default=1.0, gt=0, description="How often queued requests are looked at."
    )
    push: VoiceCallPushConfig | None = Field(
        default=None,
        description=(
            "The VoIP push used when no socket carries a request, or the phone does not "
            "answer on it in time. Unset: nothing is pushed (the notification's alert stays)."
        ),
    )

    @model_validator(mode="after")
    def _enabled_needs_an_id(self) -> VoiceCallsConfig:
        if self.enabled and not self.resident_id:
            raise ValueError(
                "gateway.channels.voice.calls.enabled needs resident_id: the phone binds a "
                "call to the resident it selected by that id"
            )
        if self.enabled and not self.host:
            raise ValueError(
                "gateway.channels.voice.calls.enabled needs host: the cluster the phone shows "
                "the call from (call.request session.host)"
            )
        return self


class VoiceChannelConfig(BaseModel):
    """Realtime voice channel — makes the resident the agent service of a voice client.

    Serves the subset of the agent-service wire (a ``{type,id,payload}``
    WebSocket at the root plus ``/api`` REST) that realtime voice clients use:
    persona, prompt, short-lived provider credentials, the resident's tools, and
    the call log. The phone's realtime model is the resident's voice; it calls
    the persona's ``voice.tools`` directly. See ``ravn.adapters.channels.voice``.

    Bind to loopback: the pod's nginx forwards ``/voice/`` here behind Envoy's
    JWT and ownership checks.
    """

    enabled: bool = Field(default=False)
    host: str = Field(default="127.0.0.1", description="Host/IP to bind the voice server.")
    port: int = Field(default=18791, description="TCP port for the voice server.")
    store_path: str = Field(
        default="~/.ravn/voice/calls.db",
        description="SQLite call store: the system of record for this resident's voice calls.",
    )
    display_name: str = Field(
        default="",
        description="Name the resident introduces itself by; defaults to the persona name.",
    )
    time_zone: str = Field(
        default="UTC",
        description="IANA time zone for the date and time in the call's prompt.",
    )
    models: dict[str, str] = Field(
        default_factory=lambda: {"grok": "grok-voice-3.0", "gpt-realtime": "gpt-realtime-2.1"},
        description="Realtime model per prompt provider (grok, gpt-realtime).",
    )
    minters: dict[str, VoiceMinterConfig] = Field(
        default_factory=dict,
        description=(
            "Credential minter per mint provider (xai, openai). A provider without a "
            "minter cannot be called; one with a minter that cannot start is fatal."
        ),
    )
    tool_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        description="Longest a voice tool call may run before it is answered as failed.",
    )
    history_default_limit: int = Field(
        default=50, ge=1, description="Calls returned by call history without a limit."
    )
    history_max_limit: int = Field(
        default=250, ge=1, description="Most calls one call-history request returns."
    )
    room_broker_url: str = Field(
        default="",
        description=(
            "The resident's Skuld broker (e.g. http://127.0.0.1:8081). When set, each "
            "call's speech is copied into the room transcript as commentary. Empty "
            "means calls stay in the voice store only."
        ),
    )
    room_participant_id: str = Field(
        default="",
        description="The resident's room participant id that agent speech is recorded under.",
    )
    room_timeout_seconds: float = Field(
        default=10.0, gt=0, description="Timeout for one room copy request."
    )
    owner_id: str = Field(
        default="",
        description=(
            "The resident's owner. Every request must carry this user in user_id_header "
            "(projected by the gateway from the verified token's sub claim): voice mints the "
            "owner's provider credentials and runs the resident's tools. Empty serves nobody."
        ),
    )
    user_id_header: str = Field(
        default="x-auth-user-id",
        description="Header the gateway projects the verified caller's user id into.",
    )
    calls: VoiceCallsConfig = Field(
        default_factory=VoiceCallsConfig,
        description="Calls the resident places itself (notify with speak).",
    )


class GatewayChannelsConfig(BaseModel):
    """Per-channel gateway configuration."""

    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    http: HttpChannelConfig = Field(default_factory=HttpChannelConfig)
    openclaw: OpenClawChannelConfig = Field(default_factory=OpenClawChannelConfig)
    skuld: SkuldChannelConfig = Field(default_factory=SkuldChannelConfig)
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    slack: SlackChannelConfig = Field(default_factory=SlackChannelConfig)
    matrix: MatrixChannelConfig = Field(default_factory=MatrixChannelConfig)
    whatsapp: WhatsAppChannelConfig = Field(default_factory=WhatsAppChannelConfig)
    voice: VoiceChannelConfig = Field(default_factory=VoiceChannelConfig)


class GatewayConfig(BaseModel):
    """Pi-mode gateway — Telegram + local HTTP access without Kubernetes.

    When enabled, Ravn runs two extra asyncio tasks:
    - A Telegram long-poll loop (no webhook, no open inbound port required).
    - A FastAPI HTTP server on localhost (or LAN IP).

    Each channel+user pair gets its own isolated agent session.
    """

    enabled: bool = Field(default=False)
    channels: GatewayChannelsConfig = Field(default_factory=GatewayChannelsConfig)
    platform: PlatformToolsConfig = Field(default_factory=PlatformToolsConfig)


class SleipnirConfig(BaseModel):
    """Sleipnir event-backbone publishing configuration (NIU-438).

    When enabled, Ravn publishes every RavnEvent to a RabbitMQ topic exchange
    so the rest of ODIN (Valkyrie attention model, Ting, monitoring) can consume
    agent output without being coupled to a specific session.

    The AMQP URL is read from an environment variable (default:
    SLEIPNIR_AMQP_URL) rather than stored directly in the config file to avoid
    accidentally committing credentials.
    """

    enabled: bool = Field(default=False)
    amqp_url_env: str = Field(default="SLEIPNIR_AMQP_URL")
    exchange: str = Field(default="ravn.events")
    agent_id: str = Field(default="")  # auto: socket.gethostname() when empty
    reconnect_delay_s: float = Field(default=5.0)
    publish_timeout_s: float = Field(default=2.0)


class TriggerAdapterConfig(BaseModel):
    """Config for a trigger adapter loaded via dotted class path.

    Supports any :class:`~ravn.ports.trigger.TriggerPort` implementation
    without modifying Ravn source code.

    Example ``ravn.yaml``::

        initiative:
          trigger_adapters:
            - adapter: mypackage.triggers.WebhookTrigger
              kwargs:
                port: 9000
                path: /hook
              secret_kwargs_env:
                token: WEBHOOK_SECRET_TOKEN
    """

    adapter: str = Field(
        description="Fully-qualified class path for the TriggerPort implementation.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Keyword arguments passed to the adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Map of kwarg name → env var name for secret values.",
    )


class BudgetConfig(BaseModel):
    """Per-Ravn daily API budget configuration (NIU-570).

    No ravn_id/tenant_id here: durable spend reporting goes through
    ``ResidentBudgetConfig`` (a dynamic adapter), and in the production
    (platform) adapter the server derives both from the caller's workload
    identity — a resident process no longer names itself for budget
    purposes, matching the trigger-poll boundary.

    Every turn is always priced (usage events carry ``cost_usd`` regardless
    of ``enabled`` — see ``DriveLoop._emit_task_usage``) and the local
    ``DailyBudgetTracker`` always records it; ``enabled`` (default ``True``)
    only gates whether the daily cap actually blocks new tasks. Pricing
    itself never needs to be conditional, because ``pricing_source``
    defaults to ``"flat"``: a per-million-token rate that prices any model,
    with no catalog lookup that can fail. ``pricing_source: "bifrost"`` is
    an explicit opt-in for catalog-accurate pricing, and only then does an
    unpriced model (checked against ``pricing_overrides`` first, then
    Bifröst's built-in catalog) raise ``UnpricedModelError`` — loud and
    fixable, exactly where the operator asked for something pricing cannot
    give. ``enabled: false`` is the only explicit opt-out from cap
    enforcement; every already-deployed config with no new values set keeps
    enforcing its cap exactly as before.
    """

    enabled: bool = Field(
        default=True,
        description=(
            "Enforce the daily cap. On by default — every already-deployed "
            "resident enforces daily_cap_usd today, and this must not silently "
            "disappear. Set to false to run this resident uncapped, an explicit "
            "operator decision, not a default."
        ),
    )
    daily_cap_usd: float = Field(
        default=1.0,
        description="Maximum USD spend per UTC day before new initiative tasks are gated.",
    )
    pricing_source: Literal["bifrost", "flat"] = Field(
        default="flat",
        description=(
            "'flat' (the default) always uses input/output_token_cost_per_million "
            "below, regardless of model — safe for any model, including ones no "
            "catalog prices. 'bifrost' is an explicit opt-in for catalog-accurate "
            "pricing: it prices each turn from pricing_overrides below, then "
            "Bifröst's built-in model-pricing catalog, and raises if the model "
            "that served the turn has no entry in either — silently billing an "
            "unpriced model at a generic flat rate would misreport spend."
        ),
    )
    pricing_overrides: dict[str, PricingOverride] = Field(
        default_factory=dict,
        description=(
            "Per-model USD/million-token pricing, keyed by model id, consulted "
            "before Bifröst's built-in snapshot when pricing_source is 'bifrost'. "
            "The remedy for UnpricedModelError on a model Bifröst's built-in "
            "catalog does not carry (e.g. Nemotron, Qwen, a fine-tune) — add it "
            "here rather than switch the whole resident to 'flat' pricing."
        ),
    )
    input_token_cost_per_million: float = Field(
        default=3.0,
        description="Input token USD/million, used only when pricing_source is 'flat'.",
    )
    output_token_cost_per_million: float = Field(
        default=15.0,
        description="Output token USD/million, used only when pricing_source is 'flat'.",
    )
    warn_at_percent: int = Field(
        default=80,
        description=(
            "Publish a DECISION warning event when daily spend reaches this percentage "
            "of the daily cap."
        ),
    )


class InitiativeConfig(BaseModel):
    """Drive-loop initiative engine configuration (NIU-539)."""

    @model_validator(mode="before")
    @classmethod
    def _refuse_removed_queue_cap(cls, data: Any) -> Any:
        """A removed cap left in a config would read as enforced while doing nothing."""
        if isinstance(data, dict) and "task_queue_max" in data:
            raise ValueError(
                "initiative.task_queue_max no longer exists: the queue no longer drops "
                "tasks when full; a backlog shows as the queue depth and each queued "
                "task's age. Remove the key"
            )
        return data

    enabled: bool = Field(
        default=False,
        description="Enable the drive loop / initiative engine.",
    )
    max_concurrent_tasks: int = Field(
        default=3,
        description="Maximum simultaneous initiative tasks.",
    )
    queue_journal_path: str = Field(
        default="~/.ravn/daemon/queue.json",
        description="Path to the queue persistence journal.",
    )
    workflow_event_dedupe_max_entries: int = Field(
        default=100_000,
        ge=1,
        description=(
            "Maximum exact workflow mesh event IDs retained in the durable queue journal. "
            "Size this for the full session so completed stages remain idempotent across "
            "transport retries and daemon restarts. Once full, the oldest entry is evicted "
            "to admit the newest one (a rolling FIFO window) rather than refusing new events."
        ),
    )
    workflow_cycle_max_entries: int = Field(
        default=100_000,
        ge=1,
        description=(
            "Maximum authoritative workflow-cycle records (one per scope/node/event_type "
            "triple) retained in the durable queue journal. There is no single point at "
            "which a workflow scope is known finished, so this is a rolling FIFO window "
            "like workflow_event_dedupe_max_entries: once full, the oldest cycle record is "
            "evicted to admit the newest one."
        ),
    )
    default_output_mode: str = Field(
        default="silent",
        description="Default output mode when not specified by a trigger.",
    )
    default_persona: str = Field(
        default="",
        description="Default persona for initiative tasks.",
    )
    heartbeat_interval_seconds: int = Field(
        default=60,
        description="Seconds between drive-loop heartbeat log entries.",
    )
    cron_tick_seconds: float = Field(
        default=30.0,
        description="Seconds between cron trigger ticks (scheduler wake interval).",
    )
    cron_max_jobs: int = Field(
        default=12,
        ge=0,
        description=(
            "Maximum enabled cron jobs one agent may hold. Further cron_create "
            "calls are refused until a job is deleted. A resident that cannot "
            "close a case schedules another check instead, so the job count "
            "grows without bound: one k8s resident reached 24 jobs firing ~87 "
            "tasks/hour, which is more than its queue could ever drain. "
            "0 disables the cap."
        ),
    )
    cron_duplicate_similarity: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Word-overlap threshold above which a new cron job counts as a "
            "restatement of an existing one and is refused. Exact-match dedup "
            "caught nothing in production because a resident never repeats "
            "itself verbatim: 20 jobs on one resident were all the same etcd "
            "check under names like 'etcd-health-check', 'recheck-etcd-latency' "
            "and 'etcd-latency-investigation'. 0 disables near-duplicate "
            "detection, leaving only exact matching."
        ),
    )
    trigger_adapters: list[TriggerAdapterConfig] = Field(
        default_factory=list,
        description=(
            "Custom trigger adapters loaded via fully-qualified class path. "
            "Supports any TriggerPort implementation without modifying Ravn source code."
        ),
    )


class PresentFileConfig(BaseModel):
    """The ``present_file`` tool: hand a document to the session's people as a file.

    In a Forge flock session the session's broker (Skuld) stages the file, makes it
    durable in Forge and records it as an artifact publication. The member asks it over
    the flock mesh, which it already speaks; Forge configures this section."""

    enabled: bool = Field(
        default=False,
        description="Offer the present_file tool (a Forge flock session sets this).",
    )
    broker_peer_id: str = Field(
        default="",
        description="Mesh peer id of the session broker that presents files (Skuld).",
    )
    timeout_s: float = Field(
        default=60.0,
        gt=0,
        description=(
            "Seconds to wait for the broker's answer. Bounded by the broker's staging copy "
            "plus its inline durable upload (Skuld presented_durable."
            "inline_upload_timeout_seconds); a timeout fails the tool and names this knob."
        ),
    )

    @model_validator(mode="after")
    def _broker_named_when_enabled(self) -> PresentFileConfig:
        if self.enabled and not self.broker_peer_id.strip():
            raise ValueError(
                "present_file.enabled needs present_file.broker_peer_id (the session "
                "broker's mesh peer id); set it, or disable present_file"
            )
        return self


class CascadeConfig(BaseModel):
    """Cascade system configuration (NIU-435).

    Controls the coordinator/flock delegation and ephemeral spawn system.
    When enabled, cascade tools (task_create, task_status, etc.) are registered
    on the daemon agent.
    """

    enabled: bool = Field(
        default=False,
        description="Enable cascade coordinator tools.",
    )
    spawn_timeout_s: float = Field(
        default=30.0,
        description="Seconds to wait for a spawned peer to register.",
    )
    collect_timeout_s: float = Field(
        default=300.0,
        description="Default timeout for task_collect (seconds).",
    )
    collect_poll_interval_s: float = Field(
        default=2.0,
        description="Polling interval for task_collect (seconds).",
    )
    mesh_delegation_timeout_s: float = Field(
        default=30.0,
        description="Timeout for mesh.send() during task delegation.",
    )
    stuck_timeout_seconds: int = Field(
        default=60,
        description="Seconds of inactivity before a sub-agent is considered stuck.",
    )
    loop_detection_threshold: int = Field(
        default=3,
        description="Number of consecutive identical tool calls that trigger loop detection.",
    )
    on_stuck: Literal["retry", "replan", "escalate", "abort"] = Field(
        default="replan",
        description="Strategy on stuck detection: retry | replan | escalate | abort.",
    )
    max_retries: int = Field(
        default=2,
        description="Maximum retry attempts before giving up (used with on_stuck=retry).",
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(default="warning")
    format: str = Field(default="text")


class NngMeshConfig(BaseModel):
    """nng transport settings for Pi-mode mesh (NIU-517)."""

    pub_sub_address: str = Field(
        default="tcp://*:7480",
        description="nng PUB/SUB listen address (e.g. tcp://*:7480 or ipc:///tmp/ravn-pub.ipc).",
    )
    req_rep_address: str = Field(
        default="tcp://*:7481",
        description="nng REQ/REP listen address (e.g. tcp://*:7481 or ipc:///tmp/ravn-rep.ipc).",
    )


class MeshSleipnirConfig(BaseModel):
    """RabbitMQ-specific mesh settings for infra-mode mesh (NIU-517)."""

    exchange: str = Field(
        default="ravn.mesh",
        description="Topic exchange used for Ravn mesh pub/sub and RPC.",
    )
    rpc_timeout_s: float = Field(
        default=10.0,
        description="Default RPC reply timeout in seconds.",
    )


class DiscoveryMdnsConfig(BaseModel):
    """mDNS-specific discovery settings (Pi mode)."""

    service_type: str = Field(
        default="_ravn._tcp.local.",
        description="mDNS service type for flock discovery. Must end with '.' per DNS-SD spec.",
    )
    handshake_timeout_s: float = Field(
        default=5.0,
        description="Seconds to wait for HMAC handshake completion.",
    )
    handshake_port: int = Field(
        default=7482,
        description=(
            "nng PAIR port used for HMAC handshake exchange. "
            "Must be unique per instance on the same host."
        ),
    )
    convergence_wait_s: float = Field(
        default=3.0,
        description=(
            "Seconds to wait after start() for mDNS announcements "
            "to arrive before querying the peer table."
        ),
    )


class DiscoverySleipnirConfig(BaseModel):
    """Sleipnir-specific discovery settings (infra mode)."""

    heartbeat_interval_s: float = Field(
        default=60.0,
        description="Seconds between Sleipnir announce heartbeats.",
    )
    convergence_wait_s: float = Field(
        default=5.0,
        description="Seconds to wait on startup for other peers to announce.",
    )
    spiffe_audience_env: str = Field(
        default="SPIFFE_TRUST_DOMAIN",
        description="Env var containing the SPIFFE trust domain for JWT-SVID validation.",
    )


class DiscoveryK8sConfig(BaseModel):
    """Kubernetes label adapter settings (infra mode cold-start)."""

    namespace: str = Field(
        default="",
        description="K8s namespace to query (empty = all namespaces).",
    )
    label_selector: str = Field(
        default="ravn.niuu.world/role=agent",
        description="Label selector used to list Ravn pods.",
    )


class DiscoveryConfig(BaseModel):
    """Flock peer detection configuration (NIU-538).

    Uses dynamic adapter loading. All adapters in the ``adapters`` list run
    simultaneously — peers are merged from all backends (union semantics).

    Example::

        discovery:
          enabled: true
          adapters:
            - adapter: ravn.adapters.discovery.mdns.MdnsDiscoveryAdapter
              handshake_port: 7482
            - adapter: ravn.adapters.discovery.k8s.K8sDiscoveryAdapter
              namespace: ravn
              label_selector: "ravn.niuu.world/role=agent"
    """

    enabled: bool = Field(
        default=False,
        description="Enable flock peer discovery.",
    )
    realm_id: str = Field(
        default="",
        description="Explicit discovery realm identity; empty uses the persisted realm key.",
    )
    adapters: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "List of discovery adapters to run simultaneously. Each entry has "
            "'adapter' (fully-qualified class path) plus adapter-specific kwargs. "
            "All adapters run in parallel; peer tables are merged."
        ),
    )
    # Common settings passed to all adapters
    heartbeat_interval_s: float = Field(
        default=30.0,
        description="Seconds between liveness heartbeats.",
    )
    peer_ttl_s: float = Field(
        default=90.0,
        description="Seconds of missed heartbeats before a peer is evicted (≈ 3 heartbeats).",
    )
    realm_id_env: str = Field(
        default="RAVN_REALM_ID",
        description="Env var carrying the realm_id for infra mode (OpenBao / K8s label).",
    )
    # Legacy fields — deprecated, use adapters list instead
    adapter: str = Field(
        default="",
        description="DEPRECATED: Use 'adapters' list instead. Legacy single-adapter mode.",
    )
    mdns: DiscoveryMdnsConfig = Field(default_factory=DiscoveryMdnsConfig)
    sleipnir: DiscoverySleipnirConfig = Field(default_factory=DiscoverySleipnirConfig)
    k8s: DiscoveryK8sConfig = Field(default_factory=DiscoveryK8sConfig)


class MeshConfig(BaseModel):
    """Ravn-to-Ravn mesh transport configuration (NIU-517).

    Uses dynamic adapter loading. All adapters in the ``adapters`` list run
    simultaneously — publish fans out to all transports, subscribe receives
    from any transport.

    Example::

        mesh:
          enabled: true
          adapters:
            - adapter: ravn.adapters.mesh.nng.NngMeshAdapter
              pub_sub_address: "ipc:///tmp/ravn-mesh/node.ipc"
              req_rep_address: "ipc:///tmp/ravn-mesh/node-rep.ipc"
            - adapter: ravn.adapters.mesh.webhook.WebhookMeshAdapter
              listen_port: 7483
              hmac_secret_env: RAVN_WEBHOOK_SECRET
    """

    enabled: bool = Field(default=False)
    adapters: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "List of mesh transport adapters to run simultaneously. Each entry has "
            "'adapter' (fully-qualified class path) plus adapter-specific kwargs. "
            "All transports are active: publish fans out to all, subscribe receives from any."
        ),
    )
    # Common settings passed to all adapters
    rpc_timeout_s: float = Field(
        default=10.0,
        description="Default RPC reply timeout in seconds.",
    )
    rpc_reply_cache_size: int = Field(
        default=DEFAULT_RPC_REPLY_CACHE_SIZE,
        ge=1,
        description=(
            "Completed RPC replies a Sleipnir mesh adapter keeps, keyed on the request "
            "event_id, so a redelivered request gets its stored reply instead of "
            "running the handler again. Keep it above the number of RPC requests this "
            "peer completes within the transport's redelivery window (JetStream: the "
            "nak backoff across max_deliver deliveries, and ack_wait)."
        ),
    )
    own_peer_id: str = Field(
        default="",
        description="This Ravn's unique mesh peer identifier (auto: hostname when empty).",
    )
    # Legacy fields — deprecated, use adapters list instead
    adapter: str = Field(
        default="",
        description="DEPRECATED: Use 'adapters' list instead. Legacy single-adapter mode.",
    )
    nng: NngMeshConfig = Field(default_factory=NngMeshConfig)
    sleipnir: MeshSleipnirConfig = Field(default_factory=MeshSleipnirConfig)
    nats: MeshNatsConfig = Field(default_factory=MeshNatsConfig)


class BuriConfig(BaseModel):
    """Búri knowledge memory substrate configuration (NIU-541).

    Controls typed fact graph, proto-RWKV session state, and proto-vMF
    embedding cluster behaviour.  Active when ``memory.backend = 'buri'``.
    """

    enabled: bool = Field(
        default=True,
        description="Enable Búri knowledge memory features.",
    )
    cluster_merge_threshold: float = Field(
        default=0.15,
        description=(
            "Cosine distance below which a new fact is merged into an existing cluster "
            "rather than creating a new one.  Lower = tighter clusters."
        ),
    )
    extraction_model: str = Field(
        default="",
        description=(
            "Model to use for fact extraction. Empty = use settings.memory.reflection_model."
        ),
    )
    min_confidence: float = Field(
        default=0.6,
        description=(
            "Facts classified with confidence below this threshold are stored as "
            "'observation' regardless of the inferred type."
        ),
    )
    supersession_cosine_threshold: float = Field(
        default=0.85,
        description=(
            "Cosine similarity threshold above which an existing fact is considered "
            "superseded by a new one (requires type match + entity overlap)."
        ),
    )


class BrowserbaseConfig(BaseModel):
    """Browserbase cloud browser configuration."""

    api_key_env: str = Field(
        default="BROWSERBASE_API_KEY",
        description="Environment variable name holding the Browserbase API key.",
    )
    project_id_env: str = Field(
        default="BROWSERBASE_PROJECT_ID",
        description="Environment variable name holding the Browserbase project ID.",
    )
    stealth: bool = Field(
        default=False,
        description="Enable stealth mode (anti-bot fingerprint masking).",
    )


class BrowserConfig(BaseModel):
    """Browser automation tool configuration (NIU-532)."""

    backend: Literal["local", "browserbase"] = Field(
        default="local",
        description=(
            "Browser backend: 'local' (headless Chromium via Playwright) "
            "or 'browserbase' (cloud execution with stealth / CAPTCHA support). "
        ),
    )
    headless: bool = Field(
        default=True,
        description="Launch browser headlessly (local backend only).",
    )
    timeout_ms: int = Field(
        default=30_000,
        description="Default navigation and action timeout in milliseconds.",
    )
    allowed_origins: list[str] = Field(
        default_factory=list,
        description=(
            "Glob patterns for hostnames the agent is allowed to navigate to. "
            "Empty list means all origins are allowed (subject to blocked_origins)."
        ),
    )
    blocked_origins: list[str] = Field(
        default_factory=list,
        description="Glob patterns for hostnames that are always blocked.",
    )
    browserbase: BrowserbaseConfig = Field(
        default_factory=BrowserbaseConfig,
        description="Browserbase cloud backend configuration.",
    )


class CheckpointConfig(BaseModel):
    """Checkpoint manager configuration (NIU-504 + NIU-537)."""

    enabled: bool = Field(
        default=True,
        description="Enable checkpoint persistence (crash-recovery and named snapshots).",
    )
    backend: Literal["local", "postgres"] = Field(
        default="local",
        description="Storage backend: 'local' (disk) or 'postgres' (infra mode).",
    )
    dir: Path = Field(
        default=Path.home() / ".ravn" / "checkpoints",
        description="Directory for disk-based checkpoint files (Pi / local mode).",
    )
    checkpoint_every_n_tools: int = Field(
        default=10,
        description=(
            "Save a named snapshot after every N tool calls (0 = disabled). "
            "Counts tool calls across all iterations in a turn."
        ),
    )
    max_checkpoints_per_task: int = Field(
        default=20,
        description="Maximum named snapshots retained per task; oldest are pruned.",
    )
    auto_before_destructive: bool = Field(
        default=True,
        description=(
            "Automatically save a named snapshot before destructive file operations "
            "(write_file, edit_file, bash with write flags)."
        ),
    )
    budget_milestone_fractions: list[float] = Field(
        default_factory=lambda: [0.5, 0.75, 0.9],
        description=(
            "Save named snapshots when iteration budget consumption crosses these "
            "fractions (e.g. 0.5 = 50%%).  Empty list disables milestone checkpointing."
        ),
    )


# ---------------------------------------------------------------------------
# NIU-558: Thread enrichment config
# ---------------------------------------------------------------------------


class ThreadConfig(BaseModel):
    """Thread enrichment queue configuration (NIU-558).

    Controls the background enricher that scores and prioritises Mímir pages
    into the wakefulness thread queue.  Disabled by default — M2 activates
    it by setting ``thread.enabled: true`` in the deployment YAML.

    All timing values and weight coefficients are overridable via environment
    variables (``RAVN_THREAD__DECAY_RATE_PER_DAY``, etc.) or the ``thread:``
    section of ``ravn.yaml``.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Enable the thread enricher.  Off until M2 activates the trigger; "
            "flip to true in the deployment ravn.yaml to start queueing."
        ),
    )
    max_queue_size: int = Field(
        default=200,
        description="Maximum number of entries held in the wakefulness thread queue.",
    )
    enricher_poll_interval_seconds: int = Field(
        default=300,
        description="How often (seconds) the enricher checks Mímir for new pages.",
    )
    enricher_llm_alias: str = Field(
        default="fast",
        description=(
            "LLM alias used by the enricher.  Should resolve to the cheapest/fastest "
            "model configured in the llm aliases map."
        ),
    )
    decay_rate_per_day: float = Field(
        default=0.05,
        description="Exponential score decay applied per calendar day of inactivity.",
    )
    recency_weight: float = Field(
        default=1.0,
        description="Weight applied to the recency signal when computing thread score.",
    )
    mention_weight: float = Field(
        default=0.3,
        description="Weight applied to the mention-count signal.",
    )
    engagement_weight: float = Field(
        default=0.5,
        description="Weight applied to the engagement signal.",
    )
    peer_weight: float = Field(
        default=0.2,
        description="Weight applied to the peer-reference signal.",
    )
    sub_thread_weight: float = Field(
        default=0.4,
        description="Weight applied to the sub-thread depth signal.",
    )
    owner_id: str | None = Field(
        default=None,
        description=(
            "This Ravn instance's ID for ownership claims on queue entries.  "
            "Defaults to the runtime instance ID when None; set explicitly in "
            "the deployment YAML so co-deployed instances can share a queue."
        ),
    )
    confidence_threshold: float = Field(
        default=0.7,
        description=(
            "Minimum LLM confidence score (0.0–1.0) required to classify a page "
            "as a thread.  Pages below this threshold are silently skipped."
        ),
    )


# ---------------------------------------------------------------------------
# NIU-565: Wakefulness trigger config
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# NIU-569: Recap trigger config
# ---------------------------------------------------------------------------


class RecapConfig(BaseModel):
    """Recap trigger configuration (NIU-569).

    Controls the trigger that fires on operator return after absence to assemble
    and surface a summary of what happened overnight (or since last interaction).

    Disabled by default — enable via ``recap.enabled: true`` in the deployment YAML.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Enable the recap trigger.  Off until explicitly activated; "
            "flip to true in the deployment ravn.yaml."
        ),
    )
    absence_threshold_seconds: int = Field(
        default=3600,
        description="Minimum absence gap (seconds) that counts as 'was away'.",
    )
    return_detection_window_seconds: int = Field(
        default=300,
        description=(
            "How recently (seconds) the operator must have interacted to count as having returned."
        ),
    )
    scheduled_recap_cron: str = Field(
        default="",
        description=(
            "Optional cron expression for a daily scheduled recap fallback "
            "(e.g. '0 7 * * *').  Empty string disables the scheduled fallback."
        ),
    )
    max_threads_in_recap: int = Field(
        default=10,
        description="Maximum number of closed threads included in a single recap.",
    )
    persona: str = Field(
        default="produce-recap",
        description="Persona used when running the recap agent task.",
    )
    channel: str = Field(
        default="",
        description="Output channel for the recap (empty = TUI only).",
    )
    poll_interval_seconds: int = Field(
        default=60,
        description="How often (seconds) the trigger checks for operator return.",
    )


class WorkflowSelectorConfig(BaseModel):
    """Select workflows from an existing workflow catalog."""

    names: list[str] = Field(
        default_factory=list,
        description="Workflow names or ids allowed by this selector.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Workflow tags allowed by this selector.",
    )
    require_all_tags: bool = Field(
        default=False,
        description="Require every configured tag instead of any matching tag.",
    )


class LearnedToolKubernetesConfig(BaseModel):
    """Deployment contract for pod-per-run learned tools."""

    namespace: str = Field(
        default="",
        description="Explicit namespace in which learned-tool Jobs are created.",
    )
    image: str = Field(
        default=(
            "ghcr.io/niuulabs/devrunner@"
            "sha256:ec7a32ffd8ca1f3ddb8bd4983198988538ab74804201ce45e14e56241adfc518"
        ),
        description="Reviewed learned-tool runner image pinned by sha256 digest.",
    )
    deny_policy_name: str = Field(
        default="",
        description="NetworkPolicy that selects denied learned-tool pods and denies egress.",
    )
    allow_policy_name: str = Field(
        default="",
        description="NetworkPolicy that selects network-enabled learned-tool pods.",
    )
    network_policy_label_key: str = Field(
        default="niuu.world/tool-network",
        description="Pod label selected by both verified learned-tool NetworkPolicies.",
    )
    job_cpu_request: str = Field(
        default="50m",
        description="CPU request for every learned-tool Job container (run() and verify()).",
    )
    job_memory_request: str = Field(
        default="64Mi",
        description="Memory request for every learned-tool Job container (run() and verify()).",
    )
    job_cpu_limit: str = Field(
        default="1",
        description="CPU limit for every learned-tool Job container (run() and verify()).",
    )
    job_memory_limit: str = Field(
        default="512Mi",
        description="Memory limit for every learned-tool Job container (run() and verify()).",
    )
    job_tmp_size: str = Field(
        default="64Mi",
        description="Size of the writable /tmp emptyDir mounted into every learned-tool Job.",
    )
    job_pod_start_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        description=(
            "Budget for a Job's pod to leave Pending and start running (scheduling, "
            "image pull) before it is treated as an infrastructure failure, for both "
            "run() and verify(). Decoupled from the tool and verify timeouts, which "
            "only start counting once the container is actually running, so a slow "
            "image pull is never confused with the tool's own code hanging."
        ),
    )
    tool_service_account: str = Field(
        default="",
        description=(
            "Service account a learned-tool Job runs as when its declared reach "
            "includes the Kubernetes API; its token is mounted only for those runs. "
            "Bind it to read-only RBAC no wider than the resident's own: it is the "
            "only cluster identity a learned tool ever holds. Empty refuses every "
            "tool that declares Kubernetes API reach."
        ),
    )
    dns_service: str = Field(
        default="",
        description=(
            "Cluster DNS Service as 'namespace/name' (e.g. "
            "'kube-system/rke2-coredns-rke2-coredns'). A Job with targeted reach gets "
            "egress to the pods behind it so it can resolve its targets. Empty refuses "
            "every tool that declares targeted network reach."
        ),
    )
    named_targets: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Words a learned tool's reach may use for an in-cluster Service, as "
            "word -> 'namespace/service[:port]' (e.g. prometheus -> "
            "'monitoring/kube-prom-kube-prometheus-prometheus:9090'). Matched as a "
            "whole word in a grant's kind or target when it names no address; "
            "nothing else is inferred from prose."
        ),
    )
    job_ttl_seconds_after_finished: int = Field(
        default=3600,
        ge=0,
        description=(
            "Kubernetes ttlSecondsAfterFinished on every learned-tool Job, as a backup "
            "sweep if the explicit post-run delete never executes (process crash). "
            "0 disables the TTL and relies solely on the explicit delete."
        ),
    )


class LearnedToolsConfig(BaseModel):
    """Learned-tool execution: where persisted tools run, and how they are built.

    Rendered under the ``resident_evolution`` key, the path deployed charts
    and Forge-written configs already use.
    """

    tool_timeout_seconds: float = Field(
        default=10.0,
        description="Hard timeout for one sandboxed resident tool execution.",
    )
    rollback_consecutive_failures: int = Field(
        default=3,
        ge=1,
        description=(
            "Consecutive implementation failures before an installed skill is "
            "auto-rolled-back (archived, regression published to the flock). "
            "Also applied by learned_tool_run, the dispatch path every "
            "learned tool actually runs through."
        ),
    )
    learned_tool_execution_backend: Literal[
        "container", "local", "forge", "devrunner", "k8s_job"
    ] = Field(
        default="container",
        description=(
            "Execution backend for learned agent tools authored through build_tool. "
            "'container' runs each invocation in a fail-closed, least-reach OCI "
            "container; 'local' runs a subprocess of the resident and enforces only "
            "the network: a tool whose declared reach is entirely local runs with every "
            "socket denied (a network namespace on Linux, a Seatbelt profile on macOS; "
            "refused where the host cannot apply it), while a networked tool gets the resident's "
            "whole network and filesystem; 'k8s_job' creates one locked-down Job per invocation "
            "after verifying its NetworkPolicies; 'forge'/'devrunner' select the "
            "legacy workspace-mounted persistent container path."
        ),
    )
    learned_tool_k8s: LearnedToolKubernetesConfig = Field(
        default_factory=LearnedToolKubernetesConfig,
        description="Pod-per-run Kubernetes execution and policy coordinates.",
    )
    learned_tool_injection_mode: Literal["dispatch", "bulk"] = Field(
        default="dispatch",
        description=(
            "How persisted learned tools reach the agent. 'dispatch' (default) keeps "
            "them out of the per-turn tool schema: they are discovered on demand via "
            "capability_list and executed by name through the single learned_tool_run "
            "tool, so prompt size stays independent of how many tools the resident "
            "has accumulated. 'bulk' restores the legacy behavior of loading every "
            "artifact as a native callable tool on every turn (unbounded prompt "
            "growth — NIU-1118)."
        ),
    )
    tool_build_adapter: str = Field(
        default="",
        description=(
            "Fully-qualified ToolBuildBackend class commissioned by build_tool "
            "when the agent supplies a build_request (for example "
            "ravn.adapters.tool_build.ForgeSessionToolBuildBackend or "
            "ravn.adapters.tool_build.a2a.A2AToolBuildBackend). Empty: the "
            "investigating agent authors tool code inline in-session. The result "
            "flows through the same review/canary/install path regardless of "
            "backend."
        ),
    )
    tool_build_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Constructor kwargs for the tool build adapter (base_url, "
            "workflow_id, external_token_env, workload token settings, model, "
            "poll intervals, ...)."
        ),
    )
    tool_builder_workflow: WorkflowSelectorConfig = Field(
        default_factory=WorkflowSelectorConfig,
        description=(
            "Optional selector for the tool-builder workflow. When configured "
            "with the A2A build backend, the backend discovers the matching "
            "workflow skill from the agent card instead of requiring a "
            "hardcoded workflow_id."
        ),
    )

    @model_validator(mode="after")
    def _validate_k8s_job_contract(self) -> LearnedToolsConfig:
        if self.learned_tool_execution_backend != "k8s_job":
            return self
        missing = [
            name
            for name, value in (
                ("namespace", self.learned_tool_k8s.namespace),
                ("deny_policy_name", self.learned_tool_k8s.deny_policy_name),
                ("allow_policy_name", self.learned_tool_k8s.allow_policy_name),
            )
            if not value.strip()
        ]
        if missing:
            raise ValueError(
                f"k8s_job learned-tool execution requires learned_tool_k8s {', '.join(missing)}"
            )
        if "@sha256:" not in self.learned_tool_k8s.image:
            raise ValueError("learned_tool_k8s.image must be pinned by sha256 digest")
        return self


# ---------------------------------------------------------------------------
# NIU-571: Trust gradient — constrains tool availability per category
# ---------------------------------------------------------------------------

TrustLevel = Literal["free", "approval", "never"]


class TrustGradientConfig(BaseModel):
    """Trust gradient policy for wakefulness personas (NIU-571).

    Each category maps to a trust level that controls whether the Ravn can
    perform the action freely, must request operator approval (by writing a
    Mímir page under ``approvals/``), or is permanently forbidden.

    Levels:
      - ``"free"``     — action is allowed without operator intervention.
      - ``"approval"`` — tool is forbidden at runtime; agent writes an
                         approval request to ``approvals/{slug}.md``.
      - ``"never"``    — tool is unconditionally forbidden.

    Override via ``trust:`` section in ``ravn.yaml`` or ``RAVN_TRUST__*``
    environment variables.
    """

    reading: TrustLevel = Field(
        default="free",
        description="Papers, docs, code, ingest.",
    )
    writing_notes: TrustLevel = Field(
        default="free",
        description="Drafts to Mímir.",
    )
    sandbox_experiments: TrustLevel = Field(
        default="free",
        description="Völundr sessions.",
    )
    consulting_peers: TrustLevel = Field(
        default="free",
        description="Bifröst cloud calls within budget.",
    )
    drafting_tickets: TrustLevel = Field(
        default="free",
        description="Linear drafts (not creation).",
    )
    producing_recaps: TrustLevel = Field(
        default="free",
        description="Recap generation.",
    )
    opening_tickets: TrustLevel = Field(
        default="approval",
        description="Creating Linear tickets.",
    )
    closing_tickets: TrustLevel = Field(
        default="approval",
        description="Closing Linear tickets.",
    )
    pushing_branches: TrustLevel = Field(
        default="approval",
        description="Git push to feature branches.",
    )
    pushing_main: TrustLevel = Field(
        default="never",
        description="Git push to main.",
    )
    external_messages: TrustLevel = Field(
        default="approval",
        description="Slack, email, Telegram to non-operator.",
    )
    spending_beyond_cap: TrustLevel = Field(
        default="approval",
        description="Exceeding daily budget.",
    )


# ---------------------------------------------------------------------------
# NIU-571: Trust gradient tool resolution
# ---------------------------------------------------------------------------

# Maps trust categories → concrete tool name prefixes.  A category may map to
# multiple tools (e.g. ``reading`` covers several tool prefixes).  Tool names
# are matched by prefix — ``"mimir"`` matches ``mimir_query``, ``mimir_write``,
# etc. — consistent with PersonaConfig's existing prefix-match convention.
TRUST_CATEGORY_TOOLS: dict[str, list[str]] = {
    "reading": ["file_read", "web_search", "web_fetch", "mimir_query", "mimir_search"],
    "writing_notes": ["mimir_write", "mimir_ingest"],
    "sandbox_experiments": ["volundr", "bash", "terminal"],
    "consulting_peers": ["cascade", "bifrost"],
    "drafting_tickets": ["linear_draft"],
    "producing_recaps": ["recap"],
    "opening_tickets": ["linear_create"],
    "closing_tickets": ["linear_close", "linear_update"],
    "pushing_branches": ["git_push"],
    "pushing_main": ["git_push_main"],
    "external_messages": ["slack", "email", "telegram"],
    "spending_beyond_cap": ["budget_override"],
}


def resolve_trust_tools(
    config: TrustGradientConfig,
    persona_allowed: list[str] | None = None,
    persona_forbidden: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Map trust gradient config to concrete allowed/forbidden tool lists.

    Returns ``(allowed, forbidden)`` where each list contains tool name
    prefixes.  The caller should apply these as additional constraints on
    top of the persona's own tool lists.

    When *persona_allowed* or *persona_forbidden* are provided, the result
    is merged via intersection semantics:

    - A tool is allowed only if **both** the trust gradient and persona
      permit it (intersection, not union).
    - A tool is forbidden if **either** the trust gradient or persona
      forbids it (union of forbidden sets).
    """
    trust_allowed: list[str] = []
    trust_forbidden: list[str] = []

    for category, level in _iter_trust_categories(config):
        tools = TRUST_CATEGORY_TOOLS.get(category, [])
        if level == "free":
            trust_allowed.extend(tools)
        else:
            # Both "approval" and "never" result in tools being forbidden
            trust_forbidden.extend(tools)

    # Merge with persona constraints
    if persona_forbidden:
        merged_forbidden = list(dict.fromkeys(trust_forbidden + persona_forbidden))
    else:
        merged_forbidden = trust_forbidden

    if persona_allowed:
        # Intersection: only tools allowed by BOTH persona and trust gradient
        trust_allowed_set = set(trust_allowed)
        all_trust = _ALL_TRUST_TOOLS
        merged_allowed = [
            t for t in persona_allowed if t in trust_allowed_set or t not in all_trust
        ]
    else:
        merged_allowed = trust_allowed

    return merged_allowed, merged_forbidden


def _iter_trust_categories(config: TrustGradientConfig):
    """Yield ``(category_name, trust_level)`` for each field."""
    for field_name in TrustGradientConfig.model_fields:
        yield field_name, getattr(config, field_name)


_ALL_TRUST_TOOLS: set[str] = {t for tools in TRUST_CATEGORY_TOOLS.values() for t in tools}


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------


class SlashCommandAdapterConfig(BaseModel):
    """Config for a custom slash command loaded via dotted class path.

    Any class implementing :class:`~ravn.ports.slash_command.SlashCommandPort`
    can be registered here and it will be available in every REPL and gateway
    session.  Custom commands are registered *after* built-ins, so they take
    precedence on name collision.

    Example ``ravn.yaml``::

        slash_commands:
          - adapter: mypackage.commands.DeployCommand
          - adapter: mypackage.commands.PagerCommand
            kwargs:
              webhook_url: "https://hooks.pagerduty.com/..."
    """

    adapter: str = Field(
        description="Fully-qualified class path for the SlashCommandPort implementation.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Keyword arguments passed to the adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Map of kwarg name → env var name for secret values.",
    )


class PersonaSourceConfig(BaseModel):
    """Config for a custom persona configuration source.

    By default Ravn loads personas from ``~/.ravn/personas/*.yaml`` and the
    built-in set.  Point ``persona_source.adapter`` at any class implementing
    :class:`~ravn.ports.persona.PersonaPort` to use a different source
    (database, remote API, generated at runtime, etc.).

    Constructor kwargs are forwarded directly to the adapter class, so the
    ``kwargs`` dict must match the adapter's ``__init__`` signature.

    Example ``ravn.yaml``::

        persona_source:
          adapter: ravn.adapters.personas.loader.FilesystemPersonaAdapter
          kwargs:
            persona_dirs: [".ravn/personas", "~/.ravn/personas"]
    """

    adapter: str = Field(
        default="ravn.adapters.personas.loader.FilesystemPersonaAdapter",
        description="Fully-qualified class path for the PersonaPort implementation.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Keyword arguments passed to the adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Map of kwarg name → env var name for secret values.",
    )


class ProfileSourceConfig(BaseModel):
    """Config for a custom deployment profile source.

    By default Ravn loads profiles from ``~/.ravn/profiles/*.yaml`` and the
    built-in set.  Point ``profile_source.adapter`` at any class implementing
    :class:`~ravn.ports.profile.ProfilePort` to use a different source
    (Kubernetes ConfigMap, database, etc.).

    Example ``ravn.yaml``::

        profile_source:
          adapter: mycompany.ravn.K8sConfigMapProfileAdapter
          kwargs:
            namespace: ravn-system
    """

    adapter: str = Field(
        default="ravn.adapters.profiles.loader.ProfileLoader",
        description="Fully-qualified class path for the ProfilePort implementation.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Keyword arguments passed to the adapter constructor.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Map of kwarg name → env var name for secret values.",
    )


class PersonaOverridesConfig(BaseModel):
    """Per-sidecar persona overrides injected by Volundr at flock dispatch time.

    Volundr embeds this block into each sidecar's ``/etc/ravn/config.yaml``
    when the flock workload has per-persona ``system_prompt_extra`` or
    ``iteration_budget`` settings.  Ravn reads them here and applies them to
    the loaded PersonaConfig via
    :func:`ravn.adapters.personas.overrides.apply_config_overrides`.

    Example sidecar YAML snippet::

        persona_overrides:
          system_prompt_extra: |
            Pay special attention to security vulnerabilities.
          iteration_budget: 40
    """

    system_prompt_extra: str = Field(
        default="",
        description=(
            "Extra system prompt text appended to the persona's system_prompt_template at runtime."
        ),
    )
    iteration_budget: int = Field(
        default=0,
        description=("Override the persona's iteration budget (0 = use persona default)."),
    )
    consumes_event_types: list[str] = Field(
        default_factory=list,
        description=(
            "Optional replacement event subscription list for the resolved persona. "
            "Empty keeps the persona's built-in consumes.event_types."
        ),
    )
    executor: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Optional executor override applied to the resolved persona at runtime. "
            "Used by workflow dispatch to select the session transport independently "
            "from the persona definition."
        ),
    )


class WorkflowRuntimeConfig(BaseModel):
    """Workflow graph metadata injected into flocked Ravn daemons.

    This is a passive runtime contract for now: Ting/Volundr pass the workflow
    graph into the flock session so mesh-aware runtime code can consume it
    without depending on Ting's pipeline compiler.
    """

    workflow_id: str = ""
    name: str = ""
    version: str = ""
    scope: str = ""
    initial_context: str = ""
    result_schema: dict[str, Any] = Field(default_factory=dict)
    graph: dict[str, Any] = Field(default_factory=dict)


class CapabilitySourceConfig(BaseModel):
    """Dynamic adapter entry for remote workflow capability discovery."""

    adapter: str = Field(
        default="",
        description="Fully-qualified WorkflowCapabilityPort implementation.",
    )
    enabled: bool = True
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class EnvironmentVocabularyConfig(BaseModel):
    """Deployment-defined signal and health vocabulary extensions.

    Values listed here are registered into the domain vocabulary registry on
    settings load. Environment ``type`` is an opaque configuration value and
    therefore needs no registration.
    """

    signal_source_kinds: list[str] = Field(
        default_factory=list,
        description="Extra signal source kinds beyond the platform defaults.",
    )
    operational_health_states: list[str] = Field(
        default_factory=list,
        description="Extra operational health states beyond the platform defaults.",
    )


class EnvironmentTopologyConfig(BaseModel):
    """Explicit Observatory topology projection for an Environment."""

    node_id: str = ""
    type_id: str = ""
    parent_id: str | None = None
    realm_id: str = ""
    cluster_id: str = ""
    host_id: str = ""
    zone: str = ""


class EnvironmentConfig(BaseModel):
    """Runtime environment identity for long-running resident Valkyries."""

    id: str = Field(
        default="local",
        description="Stable environment id, for example a k8s cluster or host id.",
    )
    name: str = Field(
        default="Local",
        description="Human-readable environment name.",
    )
    type: str = Field(
        default="local",
        description="Opaque, configuration-defined environment type.",
    )
    resident_name: str = Field(
        default="",
        description="Optional human-friendly name for the resident Valkyrie.",
    )
    topology: EnvironmentTopologyConfig = Field(
        default_factory=EnvironmentTopologyConfig,
        description=(
            "Optional explicit Observatory topology metadata. type_id defaults to the "
            "configured Environment type without interpreting it."
        ),
    )
    flocks: list[str] = Field(
        default_factory=list,
        description="Existing flock names this Valkyrie participates in.",
    )
    capability_sources: list[CapabilitySourceConfig] = Field(
        default_factory=list,
        description=(
            "Dynamic adapters that discover remote workflow capabilities from existing catalogs."
        ),
    )
    vocabulary: EnvironmentVocabularyConfig = Field(
        default_factory=EnvironmentVocabularyConfig,
        description="Deployment-defined extensions to the Environment vocabularies.",
    )

    @model_validator(mode="after")
    def _register_vocabulary(self) -> EnvironmentConfig:
        from ravn.domain.environment import extend_environment_vocabulary  # noqa: PLC0415

        extend_environment_vocabulary(
            signal_source_kinds=self.vocabulary.signal_source_kinds,
            operational_health_states=self.vocabulary.operational_health_states,
        )
        return self


class ResidentLogsConfig(BaseModel):
    """Where the logs of discovered and catalog residents are read from.

    Managed residents' logs come from their control plane. Residents deployed
    straight from infrastructure are read by placement through this dynamic
    adapter (for example
    ``ravn.adapters.resident_logs.loki.LokiResidentLogAdapter`` with
    ``base_url``). With no adapter, asking for such a resident's logs answers
    that no log source is configured.
    """

    adapter: str = Field(
        default="",
        description="Fully-qualified class path of the resident log adapter; empty for none.",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "allow"}

    def adapter_kwargs(self) -> dict[str, Any]:
        """Return constructor kwargs from explicit kwargs plus extra fields."""
        extras = self.__pydantic_extra__ or {}
        return {**self.kwargs, **extras}


class _LegacyAliasSettings(BaseSettings):
    """Settings base where legacy environment aliases override config input."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del cls, settings_cls, dotenv_settings
        return env_settings, init_settings, file_secret_settings


class RuntimeExecutorConfig(_LegacyAliasSettings):
    """Typed CLI transport overrides injected into a resident runtime."""

    volundr_api_url: str = Field(
        default="",
        validation_alias=AliasChoices("volundr_api_url", "SKULD__VOLUNDR_API_URL"),
        description="Platform origin shared with Skuld for runtime credential brokering.",
    )
    transport_adapter: str = Field(
        default="",
        validation_alias=AliasChoices(
            "transport_adapter",
            "SKULD__TRANSPORT_ADAPTER",
        ),
    )
    skip_permissions: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "skip_permissions",
            "SKULD__SKIP_PERMISSIONS",
        ),
    )
    approval_policy: str = Field(
        default="",
        validation_alias=AliasChoices(
            "approval_policy",
            "SKULD__APPROVAL_POLICY",
        ),
    )
    sandbox: str = Field(
        default="",
        validation_alias=AliasChoices(
            "sandbox",
            "SKULD__SANDBOX",
        ),
    )
    # A flock persona drives Codex through the same transport Skuld does, but
    # builds it itself, so it must resolve the same broker. Without this the
    # transport is constructed with no auth provider and the Codex CLI opens
    # its websocket unauthenticated — 401, on every persona, on every turn.
    # The env alias matches the one the chart already sets for Skuld so both
    # sides read one contract.
    codex_auth_adapter: str = Field(
        default="",
        validation_alias=AliasChoices(
            "codex_auth_adapter",
            "SKULD__CODEX_AUTH__ADAPTER",
        ),
        description=(
            "Codex auth provider for CLI transports that accept one. Empty "
            "leaves the transport unauthenticated, which is correct only for a "
            "host login."
        ),
    )
    codex_auth_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "codex_auth_kwargs",
            "SKULD__CODEX_AUTH__KWARGS",
        ),
        description="Constructor kwargs for the Codex auth provider.",
    )
    # The session's model gateway, read from the same env Skuld's own
    # transport reads. It is the address only: a session carries it whatever
    # its personas run, so it routes nothing by itself. A persona uses it only
    # when its executor binding asks for the gateway (a self-hosted model).
    model_gateway_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "model_gateway_url",
            "SKULD__MODEL_GATEWAY__URL",
        ),
        description=(
            "Model gateway base URL for personas whose executor binding sets "
            "model_gateway. Empty means this runtime has no gateway, and such a "
            "persona cannot run."
        ),
    )
    model_gateway_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "model_gateway_token",
            "SKULD__MODEL_GATEWAY__TOKEN",
        ),
        description="Bearer token presented to the model gateway alongside model_gateway_url.",
    )


class TriggerStoreConfig(BaseModel):
    """Durable storage for API-created triggers (``POST /api/v1/ravn/triggers``).

    Dynamic adapter, not a database_url/store_path pair: a plain BaseModel
    (not env-aliased) because the config file is canonical for which
    persistence backend the hosted Ravn API runs on — an env var silently
    overriding "which store" is exactly the kind of surprise this config
    should not allow.

    Empty ``adapter`` (the default) means this Ravn API process has no
    durable trigger store at all — ``/api/v1/ravn/triggers`` returns 503
    with the remedy, exactly like before this feature existed. This is
    deliberately opt-in, not opt-out: every already-deployed Ravn API chart
    (no new values set) must keep behaving exactly as it does today, and a
    silently-appearing file adapter writing into the container filesystem
    would either lose data on restart (no PVC) or silently start a new
    feature nobody asked for. The chart renders the file adapter
    (``ravn.adapters.trigger_store.FileTriggerStore``) when
    ``persistence.enabled: true``, and the Postgres adapter
    (``ravn.adapters.trigger_store.postgres_store.LazyPostgresTriggerStore``)
    when ``database.enabled: true`` — never both, and never a replica count
    above 1 with the file adapter, since several replicas each keeping their
    own file would each see a different, incomplete trigger set.
    """

    adapter: str = Field(
        default="",
        description=(
            "Fully-qualified TriggerStorePort adapter class path. Empty means "
            "this Ravn API has no durable trigger store — POST/GET/DELETE "
            "/api/v1/ravn/triggers return 503."
        ),
    )
    kwargs: dict[str, Any] = Field(
        default_factory=lambda: {"path": "~/.ravn/ravn_triggers.json"},
        description="Constructor kwargs for the adapter above.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="kwarg name -> env var name, for secrets such as a Postgres dsn.",
    )


class TriggerRepoAllowlistConfig(BaseModel):
    """Which repos each tenant's event triggers may reference.

    Closes a real cross-tenant leak: Völundr's GitHub webhook
    (``volundr.config.WebhooksConfig.github``) is ONE HMAC secret for the
    whole deployment, not one per tenant — a webhook-derived Sleipnir event
    carries no ``tenant_id`` and cannot be "stamped" with one at ingestion,
    and ``IntegrationConnection`` (a user's connected GitHub/GitLab account)
    is ``owner_id``-scoped and never persists which repos a credential can
    reach. So there is no live signal today to check a trigger's freeform
    ``repo`` field against. Until one exists, ``repo`` must be explicitly
    granted here per tenant, checked at ``POST /api/v1/ravn/triggers`` —
    without this, any tenant member could name another tenant's private repo
    and receive its PR titles, authors, and branches over the (system-wide)
    Sleipnir event bus, since ``ApiTriggerSource``'s repo filter only checks
    string equality, never authorization.

    Deliberately empty by default (fail closed): a deployment that wants
    event triggers must explicitly grant each tenant's repos. Grant ``["*"]``
    for a tenant to opt that tenant out of the check entirely — an explicit,
    auditable, single-tenant-trusted-deployment decision, not a default.
    """

    grants: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "tenant_id -> repos ('org/repo') that tenant's event triggers may "
            "reference. A tenant's list may contain '*' to allow any repo."
        ),
    )


class TriggerExecutionConfig(BaseModel):
    """Declares, for THIS hosted Ravn API deployment, whether any resident
    in it actually executes the triggers this process stores.

    This process only owns the durable trigger STORE
    (``POST``/``GET``/``DELETE /api/v1/ravn/triggers``, ``charts/ravn``) — a
    resident that polls and runs what gets stored here is a separate
    process/deployment (the Skuld chart's ``resident.triggers.enabled``,
    driven by ``volundr.adapters.outbound.flux.FluxPodManager``'s
    ``resident_triggers_enabled``/``resident_platform_base_url`` kwargs, set
    from ``charts/volundr``'s ``podManager.kwargs.*`` values).
    There is no channel from a resident process back to this one reporting
    whether it was actually configured to execute, so this cannot be
    auto-detected — an operator who turns on resident-side execution in the
    volundr and skuld charts must ALSO set this to ``true`` here, or
    ``/api/v1/ravn/settings`` and every ``POST /api/v1/ravn/triggers``
    response keep (correctly, safely) reporting execution as off.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Whether residents in this deployment actually execute stored "
            "triggers. Keep in sync with volundr's resident_triggers_enabled "
            "(FluxPodManager) and skuld's resident.triggers.enabled — this "
            "process has no way to detect either."
        ),
    )


class ResidentTriggerSourceConfig(BaseModel):
    """A resident's own poll of the durable trigger store (NIU-triggers).

    Distinct from ``initiative.cron_tick_seconds``: cron-kind triggers reuse
    the existing ``CronTrigger`` tick loop (that interval), while this one
    governs the poll for event-kind triggers and for refreshing the cron-kind
    view the ``CronTrigger`` reads from.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Have this resident load and run its own triggers from the durable "
            "trigger store (ravn.adapters.triggers.api_source.ApiTriggerSource)."
        ),
    )
    poll_interval_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Seconds between polls of the durable trigger store.",
    )
    max_consecutive_poll_failures: int = Field(
        default=5,
        gt=0,
        description=(
            "Give up (raise, ending the resident process) after this many consecutive "
            "poll failures, instead of retrying an unreachable trigger store forever "
            "with an ever-stale cache."
        ),
    )


class ResidentBudgetConfig(BaseModel):
    """How this resident reads/records its own spend (dynamic adapter).

    Production: ``ravn.adapters.resident_budget.PlatformBudgetReporter``,
    talking to the same workload-authenticated platform API the trigger poll
    uses — the server derives this resident's identity, so no ravn_id or
    tenant_id is configured here. Mini mode: explicitly configure
    ``ravn.adapters.resident_budget.LocalBudgetReporter`` with its own
    ravn_id/tenant_id/ledger kwargs — the only place a resident process still
    names itself directly, because there is no platform boundary to derive
    it from.
    """

    adapter: str = Field(
        default="",
        description=(
            "Fully-qualified ResidentBudgetPort adapter class path. Empty means "
            "this resident does not durably report spend — the daily cap still "
            "gates locally from an in-memory counter that resets on restart."
        ),
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)


class BudgetLedgerConfig(BaseModel):
    """Durable per-(tenant, ravn), per-UTC-day spend ledger backing ``/ravn/budget/*``.

    Empty ``adapter`` (the default) means no durable budget ledger —
    ``/api/v1/ravn/budget/*`` returns 503 with the remedy, exactly like
    before this feature existed; opt-in for the same reason as
    ``TriggerStoreConfig`` (an already-deployed chart with no new values set
    must keep behaving exactly as it does today). The chart renders the file
    adapter (``ravn.adapters.budget_ledger.FileBudgetLedger``) when
    ``persistence.enabled: true`` and the Postgres adapter
    (``ravn.adapters.budget_ledger.postgres_store.LazyPostgresBudgetLedger``)
    when ``database.enabled: true`` — a per-file store cannot be shared
    correctly across replicas behind an HPA.
    """

    adapter: str = Field(
        default="",
        description=(
            "Fully-qualified BudgetLedgerPort adapter class path. Empty means "
            "this Ravn API has no durable budget ledger — /api/v1/ravn/budget/* "
            "return 503."
        ),
    )
    kwargs: dict[str, Any] = Field(
        default_factory=lambda: {"path": "~/.ravn/ravn_budget_ledger.json"},
        description="Constructor kwargs for the adapter above.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="kwarg name -> env var name, for secrets such as a Postgres dsn.",
    )


class APIAuthenticationConfig(BaseModel):
    """Authentication for Ravn HTTP APIs behind the trusted Envoy proxy."""

    adapter: str = "identity.adapters.identity.EnvoyHeaderAuthenticationAdapter"
    kwargs: dict[str, Any] = Field(default_factory=dict)


class WorkflowExecutionClientConfig(BaseModel):
    """Owner-bound coordinator context injected into one developer session."""

    enabled: bool = False
    execution_id: str = ""
    base_url: str = ""
    auth_token: str = Field(default="", repr=False)
    auth_token_file: str = ""


class Settings(BaseSettings):
    """Ravn application settings.

    Loaded from YAML with RAVN_ environment variable overrides.
    Precedence: env vars > yaml file > defaults.
    """

    api_auth: APIAuthenticationConfig = Field(default_factory=APIAuthenticationConfig)
    workflow_execution: WorkflowExecutionClientConfig = Field(
        default_factory=WorkflowExecutionClientConfig
    )

    model_config = SettingsConfigDict(
        yaml_file_encoding="utf-8",
        env_prefix="RAVN_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # Core sections
    agent: AgentConfig = Field(default_factory=AgentConfig)

    # New NIU-427 sections
    context: ContextConfig = Field(default_factory=ContextConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    permission: PermissionConfig = Field(default_factory=PermissionConfig)
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    mcp_token_store: MCPTokenStoreConfig = Field(default_factory=MCPTokenStoreConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    channels: list[ChannelConfig] = Field(
        default_factory=list,
        deprecated="Use gateway.channels instead. This field is ignored.",
    )

    # NIU-431: context management
    iteration_budget: IterationBudgetConfig = Field(default_factory=IterationBudgetConfig)
    context_management: ContextManagementConfig = Field(default_factory=ContextManagementConfig)

    # NIU-436: semantic memory
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    reranker: RerankerConfig = Field(default_factory=RerankerConfig)
    skill: SkillConfig = Field(default_factory=SkillConfig)

    # NIU-516: Pi-mode gateway
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)

    # Skuld broker channel — delivers mesh events to browser UI
    skuld: SkuldChannelConfig = Field(default_factory=SkuldChannelConfig)

    # NIU-438: Sleipnir event backbone
    sleipnir: SleipnirConfig = Field(default_factory=SleipnirConfig)

    # NIU-539: drive loop / initiative engine
    initiative: InitiativeConfig = Field(default_factory=InitiativeConfig)

    # NIU-570: daily API budget gates
    budget: BudgetConfig = Field(default_factory=BudgetConfig)

    # Durable storage backing /api/v1/ravn/triggers and /api/v1/ravn/budget/*
    trigger_store: TriggerStoreConfig = Field(default_factory=TriggerStoreConfig)
    trigger_repo_allowlist: TriggerRepoAllowlistConfig = Field(
        default_factory=TriggerRepoAllowlistConfig
    )
    trigger_execution: TriggerExecutionConfig = Field(default_factory=TriggerExecutionConfig)
    budget_ledger: BudgetLedgerConfig = Field(default_factory=BudgetLedgerConfig)
    resident_triggers: ResidentTriggerSourceConfig = Field(
        default_factory=ResidentTriggerSourceConfig
    )
    resident_budget: ResidentBudgetConfig = Field(default_factory=ResidentBudgetConfig)

    # NIU-558: thread enrichment queue
    thread: ThreadConfig = Field(default_factory=ThreadConfig)

    # NIU-569: recap trigger
    recap: RecapConfig = Field(default_factory=RecapConfig)

    # Learned-tool execution and build commissioning
    resident_evolution: LearnedToolsConfig = Field(default_factory=LearnedToolsConfig)

    # NIU-571: trust gradient — constrains wakefulness tool availability
    trust: TrustGradientConfig = Field(default_factory=TrustGradientConfig)

    # NIU-541: Búri knowledge memory substrate
    buri: BuriConfig = Field(default_factory=BuriConfig)

    # NIU-540: Mímir persistent knowledge base
    mimir: MimirConfig = Field(default_factory=MimirConfig)

    # NIU-517: Ravn-to-Ravn mesh transport
    mesh: MeshConfig = Field(default_factory=MeshConfig)

    # NIU-538: flock peer discovery
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)

    resident_logs: ResidentLogsConfig = Field(default_factory=ResidentLogsConfig)
    platform_tenant_id: str = Field(
        default=DEFAULT_PLATFORM_TENANT_ID,
        description=(
            "The org that runs this deployment. It owns anything that names no org, "
            "and it alone may use the Valkyrie dashboard API."
        ),
    )

    # NIU-435: cascade coordinator / flock delegation / ephemeral spawn
    cascade: CascadeConfig = Field(default_factory=CascadeConfig)

    # Documents a member hands the session's people through its broker (Forge artifacts).
    present_file: PresentFileConfig = Field(default_factory=PresentFileConfig)

    # NIU-504/NIU-537: task interruption / resume / named checkpoint snapshots
    checkpoint: CheckpointConfig = Field(default_factory=CheckpointConfig)

    # NIU-532: browser automation
    browser: BrowserConfig = Field(default_factory=BrowserConfig)

    # Plugin extension points
    slash_commands: list[SlashCommandAdapterConfig] = Field(
        default_factory=list,
        description="Custom slash command adapters loaded via dotted class path.",
    )
    persona_source: PersonaSourceConfig = Field(
        default_factory=PersonaSourceConfig,
        description="Persona configuration source adapter.",
    )
    profile_source: ProfileSourceConfig = Field(
        default_factory=ProfileSourceConfig,
        description="Deployment profile source adapter.",
    )

    # NIU-638: per-sidecar overrides injected by Volundr at flock dispatch
    persona_overrides: PersonaOverridesConfig = Field(
        default_factory=PersonaOverridesConfig,
        description="Per-sidecar persona overrides injected by Volundr at flock dispatch.",
    )
    workflow: WorkflowRuntimeConfig = Field(
        default_factory=WorkflowRuntimeConfig,
        description="Workflow graph payload injected for flock-backed sessions.",
    )
    environment: EnvironmentConfig = Field(
        default_factory=EnvironmentConfig,
        description="Environment identity and signal subscriptions for resident Valkyries.",
    )
    runtime_executor: RuntimeExecutorConfig = Field(
        default_factory=RuntimeExecutorConfig,
        description="Resident CLI transport overrides supplied by the session runtime.",
    )
    runtime_persona: str = Field(
        default="default",
        validation_alias=AliasChoices(
            "runtime_persona",
            "RAVN_PERSONA",
        ),
        description="Persona selected for this resident runtime.",
    )
    state_dir: str = Field(
        default="",
        validation_alias=AliasChoices(
            "state_dir",
            "RAVN_STATE_DIR",
        ),
        description="Writable resident state directory; empty uses workspace or home.",
    )

    # Legacy — kept so existing CLI wiring (NIU-426) continues to work
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

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
            YamlConfigSettingsSource(settings_cls, yaml_file=_config_paths()),
            file_secret_settings,
        )

    def effective_model(self) -> str:
        """Return the resolved model name.

        Prefers ``llm.model``.  Falls back to ``agent.model`` when
        ``llm.model`` is at its default but ``agent.model`` has been
        explicitly set (backward-compat with pre-consolidation configs).
        """
        _default = "claude-sonnet-4-6"
        if self.llm.model != _default:
            return self.llm.model
        if self.agent.model != _default:
            import logging as _log

            _log.getLogger(__name__).warning(
                "agent.model is deprecated — use llm.model instead",
            )
            return self.agent.model
        return _default

    @staticmethod
    def _uses_effective_agent_model(value: str | None) -> bool:
        if value is None:
            return True
        return value.strip().lower() in {"", "default", "agent", "same-as-agent"}

    def effective_memory_reflection_model(self) -> str:
        """Return the model for compact post-task reflections."""

        configured = self.memory.reflection_model
        if self._uses_effective_agent_model(configured):
            return self.effective_model()
        return configured

    def prompt_ceiling_tokens(self) -> int:
        """The hard prompt ceiling: configured, else derived from the context window."""
        cm = self.context_management
        if cm.max_prompt_tokens > 0:
            return cm.max_prompt_tokens
        if cm.context_window_tokens <= 0:
            return 0
        output_cap = (
            self.llm.max_tokens if self.llm.max_tokens is not None else self.agent.max_tokens
        )
        if output_cap is None:
            return cm.context_window_tokens
        return max(0, cm.context_window_tokens - output_cap)

    def compaction_budget_tokens(self) -> int:
        """The prompt size compaction aims under: the latency target, else the ceiling."""
        target = self.context_management.compaction_target_tokens
        return target if target > 0 else self.prompt_ceiling_tokens()

    def effective_max_tokens(self) -> int | None:
        """Return the resolved answer cap, or ``None`` for no cap.

        ``llm.max_tokens`` wins; the deprecated ``agent.max_tokens`` is read
        only when ``llm.max_tokens`` is unset.
        """
        if self.llm.max_tokens is not None:
            return self.llm.max_tokens
        if self.agent.max_tokens is not None:
            import logging as _log

            _log.getLogger(__name__).warning(
                "agent.max_tokens is deprecated — use llm.max_tokens instead",
            )
            return self.agent.max_tokens
        return None


# ---------------------------------------------------------------------------
# Project-level config overlay (RAVN.md)
# ---------------------------------------------------------------------------


def _safe_int(val: object, default: int = 0) -> int:
    """Convert *val* to int, returning *default* on ValueError/TypeError."""
    try:
        return int(val)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def _safe_bool(val: object, default: bool = False) -> bool:
    """Convert *val* to bool, returning *default* on unrecognised input."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "yes", "1", "on")
    if isinstance(val, int):
        return bool(val)
    return default


# ---------------------------------------------------------------------------
# Valid schema values
# ---------------------------------------------------------------------------

_VALID_PERSONAS: frozenset[str] = frozenset(
    {
        "coding-agent",
        "assistant",
        "researcher",
        "reviewer",
        "planner",
    }
)


@dataclass
class ProjectConfig:
    """Project-level configuration overlay parsed from a RAVN.md file.

    When Ravn starts in a directory containing RAVN.md it reads this as a
    lightweight config overlay on top of the global Settings.  Only fields
    explicitly present in the file are populated; absent fields keep their
    zero/empty defaults so callers can safely merge them with Settings.

    Format (Markdown header + YAML body)::

        # RAVN Project: my-service

        persona: coding-agent
        allowed_tools: [file, git, terminal, web]
        forbidden_tools: [volundr, cascade]
        permission_mode: workspace-write
        primary_alias: balanced
        thinking_enabled: true
        iteration_budget: 30
        notes: >
          This is a FastAPI service. Always run tests before committing.

    Attributes:
        warnings: Non-fatal schema validation messages populated by
            ``from_text()``.  Callers may display these to the user.
    """

    project_name: str = ""
    persona: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    permission_mode: str = ""
    primary_alias: str = ""
    thinking_enabled: bool = False
    iteration_budget: int = 0
    notes: str = ""
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_text(cls, text: str) -> ProjectConfig:
        """Parse RAVN.md *text* into a ProjectConfig.

        The file must start with a ``# RAVN Project: <name>`` header.
        Everything after the header is treated as YAML.  If the header is
        absent or the YAML is malformed the method returns an empty
        ProjectConfig rather than raising.

        Schema validation warnings are stored in ``ProjectConfig.warnings``.
        An unrecognised ``permission_mode`` raises :class:`ValueError` instead:
        a permission boundary is never downgraded to a warning.
        """
        import yaml  # PyYAML — present via pydantic-settings[yaml]

        project_name = ""
        yaml_lines: list[str] = []
        past_header = False

        for line in text.splitlines():
            if not past_header:
                if line.startswith("# RAVN Project:"):
                    project_name = line[len("# RAVN Project:") :].strip()
                    past_header = True
                continue
            yaml_lines.append(line)

        raw: dict = {}
        if yaml_lines:
            with suppress(Exception):
                parsed = yaml.safe_load("\n".join(yaml_lines))
                if isinstance(parsed, dict):
                    raw = parsed

        warnings: list[str] = []

        # A permission mode that cannot be parsed is fatal, never a warning:
        # carrying an unrecognised mode forward is how a read-only boundary
        # silently became writable.
        raw_permission_mode = raw.get("permission_mode")
        parse_optional_permission_mode(raw_permission_mode)
        permission_mode = "" if raw_permission_mode is None else str(raw_permission_mode)

        persona = str(raw.get("persona", ""))
        if persona and persona not in _VALID_PERSONAS:
            warnings.append(
                f"Unknown persona {persona!r}. Known personas: {sorted(_VALID_PERSONAS)}"
            )

        iteration_budget = _safe_int(raw.get("iteration_budget", 0))
        if iteration_budget < 0:
            warnings.append(f"iteration_budget must be >= 0, got {iteration_budget}. Using 0.")
            iteration_budget = 0

        return cls(
            project_name=project_name,
            persona=persona,
            allowed_tools=list(raw.get("allowed_tools", [])),
            forbidden_tools=list(raw.get("forbidden_tools", [])),
            permission_mode=permission_mode,
            primary_alias=str(raw.get("primary_alias", "")),
            thinking_enabled=_safe_bool(raw.get("thinking_enabled", False)),
            iteration_budget=iteration_budget,
            notes=str(raw.get("notes", "")),
            warnings=warnings,
        )

    @classmethod
    def load(cls, path: Path) -> ProjectConfig | None:
        """Load a ProjectConfig from *path*, or None if the file is unreadable."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return cls.from_text(text)

    @classmethod
    def discover(cls, cwd: Path | None = None) -> ProjectConfig | None:
        """Walk from *cwd* toward the filesystem root looking for RAVN.md.

        Discovery order:
        1. *cwd* (defaults to ``Path.cwd()``)
        2. Each ancestor directory up to the filesystem root
        3. ``~/.ravn/default.md`` — user-level global default

        Returns the first ``ProjectConfig`` found, or ``None`` if no file
        exists in any of the above locations.
        """
        start = Path(cwd) if cwd is not None else Path.cwd()
        current = start.resolve()
        while True:
            candidate = current / "RAVN.md"
            if candidate.is_file():
                return cls.load(candidate)
            parent = current.parent
            if parent == current:
                break
            current = parent

        # Global user default — ~/.ravn/default.md
        global_default = Path.home() / ".ravn" / "default.md"
        if global_default.is_file():
            return cls.load(global_default)

        return None
