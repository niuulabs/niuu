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

import json
import os
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, model_validator
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from niuu.config_models import WorkloadIdentityVerifierConfig
from niuu.domain.observability import ObservabilityConfig
from niuu.mesh.config import MeshNatsConfig

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
    max_tokens: int = Field(default=8192)
    max_retries: int = Field(default=3)
    retry_base_delay: float = Field(default=1.0)
    timeout: float = Field(default=120.0)
    provider: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
    fallbacks: list[LLMProviderConfig] = Field(
        default_factory=list,
        description="Ordered list of fallback providers tried when the primary fails.",
    )
    extended_thinking: ExtendedThinkingConfig = Field(
        default_factory=ExtendedThinkingConfig,
        description="Extended thinking (deliberate reasoning budget) configuration.",
    )


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


class EmbeddingConfig(BaseModel):
    """Embedding backend configuration for semantic memory search."""

    enabled: bool = Field(
        default=False,
        description="Enable embedding-based semantic search in episodic memory.",
    )
    adapter: str = Field(
        default="ravn.adapters.embedding.sentence_transformer.SentenceTransformerEmbeddingAdapter",
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
    reflection_max_tokens: int = Field(
        default=512,
        description="Maximum tokens for the reflection LLM call.",
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

    mode: Literal[
        "read_only",
        "workspace_write",
        "full_access",
        "prompt",
        # Legacy aliases kept for backwards compatibility
        "allow_all",
        "deny_all",
    ] = Field(
        default="workspace_write",
        description=(
            "Permission mode: "
            "'read_only' (no mutations), "
            "'workspace_write' (writes within workspace only), "
            "'full_access' (unrestricted, explicit opt-in), "
            "'prompt' (interactive confirmation per action)."
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
    max_tokens: int = Field(default=8192)
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


class ContextManagementConfig(BaseModel):
    """Context compression and prompt-builder configuration."""

    context_window_tokens: int = Field(
        default=0,
        ge=0,
        description=(
            "Model context-window size in tokens. The runtime reserves the configured "
            "maximum output before deriving the safe prompt budget. 0 means the model "
            "window is unknown, so max_prompt_tokens must provide the prompt ceiling."
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
    compression_max_tokens: int = Field(
        default=1024,
        description="Max tokens for compaction document generation.",
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
        description=(
            "Hard per-call budget for the estimated prompt size (system prompt + "
            "tool schemas + message history), in tokens. 0 disables the check. "
            "When a turn's estimated prompt still exceeds this after context "
            "compression, the LLM call is refused with PromptBudgetExceededError "
            "and a per-section breakdown — fail loud instead of sending a request "
            "that overflows the model's context window."
        ),
    )

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


class LLMAdapterConfig(BaseModel):
    """Dynamic LLM adapter configuration (legacy; prefer llm.provider)."""

    adapter: str = Field(
        default="ravn.adapters.llm.anthropic.AnthropicAdapter",
        description="Fully-qualified class path for the LLM adapter.",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    max_retries: int = Field(default=3)
    retry_base_delay: float = Field(default=1.0)
    timeout: float = Field(default=120.0)


class EvolutionConfig(BaseModel):
    """Self-improvement loop configuration (NIU-501).

    Controls when the pattern extraction pass runs and how many samples it
    analyses when looking for recurring tool sequences, error patterns, and
    effective strategies.
    """

    enabled: bool = Field(
        default=True,
        description="Enable the self-improvement pattern extraction pass.",
    )
    min_new_outcomes: int = Field(
        default=10,
        description=(
            "Minimum number of new outcomes recorded since the last extraction "
            "before the pass is triggered automatically on startup."
        ),
    )
    state_path: str = Field(
        default="~/.ravn/evolution_state.json",
        description="Path to the JSON file that persists the last-run state.",
    )
    max_episodes_to_analyze: int = Field(
        default=100,
        description="Maximum number of episodes loaded per extraction pass.",
    )
    max_outcomes_to_analyze: int = Field(
        default=50,
        description="Maximum number of task outcomes loaded per extraction pass.",
    )
    skill_suggestion_min_occurrences: int = Field(
        default=3,
        description="Minimum times a tool pattern must appear before a skill is suggested.",
    )
    error_warning_min_occurrences: int = Field(
        default=3,
        description="Minimum times an error keyword must appear before a warning is proposed.",
    )
    strategy_min_occurrences: int = Field(
        default=3,
        description=(
            "Minimum times a domain tag must appear in SUCCESS episodes "
            "before a strategy injection is proposed."
        ),
    )
    max_skill_suggestions: int = Field(
        default=5,
        description="Maximum skill suggestions to include in one evolution proposal.",
    )
    max_system_warnings: int = Field(
        default=5,
        description="Maximum system-prompt warnings to include in one proposal.",
    )
    max_strategy_injections: int = Field(
        default=3,
        description="Maximum strategy injections to include in one proposal.",
    )


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
        description="Maximum characters per outbound Telegram message (API limit).",
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
    operator_token_env: str = Field(
        default="RAVN_OPERATOR_TOKEN",
        description=(
            "Environment variable containing the bearer token required by resident "
            "operator-question and answer endpoints. Missing tokens disable those endpoints."
        ),
    )
    a2a_push_enabled: bool = Field(
        default=False,
        description="Accept authenticated A2A task callbacks and wake the resident.",
    )
    a2a_push_auth: WorkloadIdentityVerifierConfig = Field(
        default_factory=lambda: WorkloadIdentityVerifierConfig(name="a2a-push", adapter=""),
        description="JWT verifier used to authenticate A2A callback workloads.",
    )
    a2a_push_required_claims: dict[str, Any] = Field(
        default_factory=dict,
        description="Exact JWT claims required on an authenticated A2A callback.",
    )
    a2a_push_max_body_bytes: int = Field(
        default=1_048_576,
        ge=1024,
        description="Maximum accepted A2A callback body size.",
    )
    resident_hud_enabled: bool = Field(
        default=False,
        description="Expose the resident's read-only HUD and live data endpoints.",
    )
    resident_hud_poll_interval_seconds: float = Field(
        default=2.0,
        gt=0,
        description="Seconds between resident HUD data refreshes.",
    )
    resident_hud_stale_after_seconds: float = Field(
        default=15.0,
        gt=0,
        description="Seconds without a successful refresh before the resident HUD reports stale.",
    )
    resident_hud_activity_max_events: int = Field(
        default=20,
        ge=1,
        description="Maximum recent in-flight activity events returned per resident task.",
    )
    resident_hud_recent_tasks: int = Field(
        default=5,
        ge=1,
        description="Maximum completed tasks retained in the resident HUD progress history.",
    )
    resident_hud_task_context_max_chars: int = Field(
        default=4000,
        ge=200,
        description=(
            "Maximum characters of bounded active-task observations and objectives "
            "returned to the resident HUD."
        ),
    )
    resident_hud_trace_url_template: str = Field(
        default="",
        description=(
            "Optional operator UI URL containing {trace_id}; the HUD links factual "
            "judgments to their distributed trace when configured."
        ),
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
    pat_token: str = Field(
        default="",
        description=(
            "Explicit external bearer token for platform API authentication. "
            "In-cluster Ravn/Valkyrie callers should leave this empty and use "
            "projected workload identity."
        ),
    )
    workload_token_file: str = Field(
        default="/var/run/secrets/kubernetes.io/serviceaccount/token",
        description="Projected workload identity token file used when pat_token is not set.",
    )
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
    a2a_push_callback_url: str = Field(
        default="",
        description=("Public HTTPS callback registered for A2A tasks that advertise push support."),
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
        description="Maximum characters per outbound Discord message (API limit).",
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
        description="Maximum characters per outbound Slack message before truncation.",
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
        description="Maximum characters per outbound Matrix message.",
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
        description="Maximum characters per outbound WhatsApp message.",
    )
    api_base: str = Field(
        default="https://graph.facebook.com/v18.0",
        description="Meta Graph API base URL.",
    )


class GatewayChannelsConfig(BaseModel):
    """Per-channel gateway configuration."""

    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    http: HttpChannelConfig = Field(default_factory=HttpChannelConfig)
    skuld: SkuldChannelConfig = Field(default_factory=SkuldChannelConfig)
    discord: DiscordChannelConfig = Field(default_factory=DiscordChannelConfig)
    slack: SlackChannelConfig = Field(default_factory=SlackChannelConfig)
    matrix: MatrixChannelConfig = Field(default_factory=MatrixChannelConfig)
    whatsapp: WhatsAppChannelConfig = Field(default_factory=WhatsAppChannelConfig)


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
    """Per-Ravn daily API budget configuration (NIU-570)."""

    daily_cap_usd: float = Field(
        default=1.0,
        description="Maximum USD spend per UTC day before new initiative tasks are gated.",
    )
    input_token_cost_per_million: float = Field(
        default=3.0,
        description="Input token cost in USD per million tokens (used to estimate task cost).",
    )
    output_token_cost_per_million: float = Field(
        default=15.0,
        description="Output token cost in USD per million tokens (used to estimate task cost).",
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

    enabled: bool = Field(
        default=False,
        description="Enable the drive loop / initiative engine.",
    )
    max_concurrent_tasks: int = Field(
        default=3,
        description="Maximum simultaneous initiative tasks.",
    )
    task_queue_max: int = Field(
        default=50,
        description="Maximum tasks in the priority queue.",
    )
    queue_journal_path: str = Field(
        default="~/.ravn/daemon/queue.json",
        description="Path to the queue persistence journal.",
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
    trigger_adapters: list[TriggerAdapterConfig] = Field(
        default_factory=list,
        description=(
            "Custom trigger adapters loaded via fully-qualified class path. "
            "Supports any TriggerPort implementation without modifying Ravn source code."
        ),
    )


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


class MimirSearchConfig(BaseModel):
    """Mímir search backend configuration."""

    backend: Literal["fts", "vector"] = Field(
        default="fts",
        description="Search backend: 'fts' (full-text search) or 'vector' (embedding-based).",
    )


class MimirAuthConfig(BaseModel):
    """Auth configuration for a remote Mímir instance."""

    type: Literal["bearer", "workload", "spiffe"] = Field(
        default="bearer",
        description=(
            "Auth mechanism: 'bearer' (dev), 'workload' (projected JWT exchange), "
            "or 'spiffe' (production mTLS)."
        ),
    )
    token: str | None = Field(
        default=None,
        description="Bearer token value (when type=bearer).",
    )
    token_file: str | None = Field(
        default=None,
        description="File containing a bearer token (when type=bearer).",
    )
    token_env: str | None = Field(
        default=None,
        description="Environment variable containing a bearer token (when type=bearer).",
    )
    exchange_url: str | None = Field(
        default=None,
        description="Workload token exchange URL (when type=workload).",
    )
    audiences: list[str] = Field(
        default_factory=lambda: ["mimir"],
        description="Target audiences requested from workload token exchange.",
    )
    trust_domain: str | None = Field(
        default=None,
        description="SPIFFE trust domain (when type=spiffe), e.g. 'niuu.world'.",
    )


class MimirInstanceConfig(BaseModel):
    """Configuration for a single Mímir instance in the composite adapter.

    Either ``path`` (local filesystem) or ``url`` (HTTP service) must be set.
    """

    name: str = Field(description="Logical name, e.g. 'local', 'shared', 'kanuck'.")
    role: Literal["shared", "local", "domain"] = Field(
        default="local",
        description="Instance role.",
    )
    path: str | None = Field(
        default=None,
        description="Filesystem root for a local MarkdownMimirAdapter.",
    )
    url: str | None = Field(
        default=None,
        description="Base URL for a remote HttpMimirAdapter.",
    )
    auth: MimirAuthConfig | None = Field(
        default=None,
        description="Auth config for remote instances.",
    )
    categories: list[str] | None = Field(
        default=None,
        description="Category filter for domain-scoped Mímirs. None means all categories.",
    )
    read_priority: int = Field(
        default=0,
        description="Read order — lower value is queried first (local=0, shared=1, domain=2).",
    )


class MimirWriteRoutingConfig(BaseModel):
    """Config-driven write routing for the CompositeMimirAdapter.

    Example YAML::

        write_routing:
          rules:
            - prefix: "self/"
              mounts: ["local"]
            - prefix: "technical/"
              mounts: ["local", "shared"]
            - prefix: "household/"
              mounts: ["shared"]
          default: ["local"]
    """

    rules: list[dict] = Field(
        default_factory=list,
        description="Ordered list of {prefix, mounts} routing rules.",
    )
    default: list[str] = Field(
        default_factory=lambda: ["local"],
        description="Default mount(s) when no prefix matches.",
    )


class MimirSourceTriggerConfig(BaseModel):
    """Config for the source-ingest synthesis trigger."""

    enabled: bool = Field(
        default=True,
        description="Enable automatic synthesis when unprocessed sources are detected.",
    )
    poll_interval_seconds: int = Field(
        default=60,
        description="How often (seconds) to poll for unprocessed raw sources.",
    )
    persona: str = Field(
        default="mimir-curator",
        description="Persona to use for synthesis tasks.",
    )
    max_tokens: int | None = Field(
        default=4096,
        description=(
            "Maximum output tokens for synthesis tasks. "
            "Set lower than the LLM default to leave room for large source documents "
            "in the context window. None uses the LLM/settings default."
        ),
    )
    max_content_chars: int = Field(
        default=120_000,
        description=(
            "Maximum raw source characters injected into synthesis task context. "
            "Large web captures can otherwise exhaust the model context before "
            "the agent can write any synthesis."
        ),
    )
    retry_after_seconds: int = Field(
        default=600,
        description=(
            "Seconds to wait before re-enqueuing a source whose previous synthesis "
            "task failed or did not complete. Prevents infinite retry storms while "
            "still recovering from transient failures."
        ),
    )


class MimirStalenessTriggerConfig(BaseModel):
    """Config for the staleness refresh trigger."""

    enabled: bool = Field(
        default=True,
        description="Enable periodic staleness checks on frequently-used pages.",
    )
    schedule_hours: int = Field(
        default=6,
        description="How often (hours) to run the staleness check.",
    )
    top_n: int = Field(
        default=20,
        description="Number of most-frequently-accessed pages to check for staleness.",
    )
    persona: str = Field(
        default="mimir-curator",
        description="Persona to use for refresh tasks.",
    )
    max_tokens: int | None = Field(
        default=4096,
        description=(
            "Maximum output tokens for staleness refresh tasks. None uses the LLM/settings default."
        ),
    )


class MimirIngestConfig(BaseModel):
    """Configuration for the Mímir ingest entity detection step (NIU-578)."""

    entity_detection: bool = Field(
        default=True,
        description="Enable LLM-based entity extraction during ingest.",
    )
    entity_model: str = Field(
        default="claude-haiku-4-5-20251001",
        description="LLM model alias used for entity extraction (prefer cheap/fast).",
    )
    entity_max_tokens: int = Field(
        default=1024,
        description="Maximum output tokens for the entity extraction LLM call.",
    )


class MimirReflexConfig(BaseModel):
    """Retrieval reflex — deterministic Mímir entity pointer injection (NIU-1059).

    A zero-LLM scanner that matches incoming agent-turn messages against the
    Mímir entity feed and prefixes compact pointers (never page bodies).
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Enable automatic retrieval-reflex pointer injection on agent turns. "
            "Disabled by default; agents can still query Mímir explicitly."
        ),
    )
    max_pointers: int = Field(
        default=5,
        description="Maximum number of entity pointers injected per turn.",
    )
    cache_ttl_seconds: int = Field(
        default=300,
        description="How long (seconds) the entity index is cached before refetching.",
    )
    base_url: str = Field(
        default="",
        description=(
            "Base URL of the Mímir HTTP service exposing GET /mimir/entities/index. "
            "When empty, falls back to the first mimir.instances entry with a url."
        ),
    )
    timeout_seconds: float = Field(
        default=5.0,
        description="HTTP timeout (seconds) for the entity feed request.",
    )


class MimirConfig(BaseModel):
    """Mímir persistent compounding knowledge base configuration (NIU-540).

    Mímir maintains a filesystem-backed wiki under ``path/wiki/`` and stores
    raw immutable sources under ``path/raw/``.  The schema file ``MIMIR.md``
    is seeded on first run if it does not exist.

    Auto-distillation fires after sessions that meet the distillation criteria
    (session duration, web calls made, documents written).  Idle lint fires
    when the drive loop queue is empty and no human activity for
    ``idle_lint_threshold_minutes``.
    """

    enabled: bool = Field(
        default=True,
        description="Enable the Mímir knowledge base.",
    )
    path: str = Field(
        default="~/.ravn/mimir",
        description="Root directory for the Mímir knowledge base.",
    )
    auto_distill: bool = Field(
        default=True,
        description=(
            "Automatically distil session knowledge into the wiki after qualifying sessions."
        ),
    )
    distill_min_session_minutes: int = Field(
        default=5,
        description="Minimum session duration (minutes) before auto-distillation is triggered.",
    )
    idle_lint_threshold_minutes: int = Field(
        default=60,
        description=(
            "Minutes of drive-loop idle time + no human activity before the lint pass fires."
        ),
    )
    continuation_threshold_minutes: int = Field(
        default=30,
        description=(
            "Minutes since the last human message before an abandoned session is continued."
        ),
    )
    categories: list[str] = Field(
        default_factory=lambda: ["technical", "projects", "research", "household", "self"],
        description="Top-level wiki categories.",
    )
    search: MimirSearchConfig = Field(
        default_factory=MimirSearchConfig,
        description="Search backend configuration.",
    )
    instances: list[MimirInstanceConfig] = Field(
        default_factory=list,
        description=(
            "Multi-Mímir instances for CompositeMimirAdapter. "
            "When empty, a single local MarkdownMimirAdapter is used (path=mimir.path). "
            "Each entry is either a local filesystem adapter (path=) "
            "or a remote HTTP adapter (url=)."
        ),
    )
    write_routing: MimirWriteRoutingConfig = Field(
        default_factory=MimirWriteRoutingConfig,
        description="Write routing rules for the CompositeMimirAdapter.",
    )
    source_trigger: MimirSourceTriggerConfig = Field(
        default_factory=MimirSourceTriggerConfig,
        description="Automatic synthesis trigger for unprocessed raw sources.",
    )
    staleness_trigger: MimirStalenessTriggerConfig = Field(
        default_factory=MimirStalenessTriggerConfig,
        description="Scheduled staleness refresh for frequently-used pages.",
    )
    ingest: MimirIngestConfig = Field(
        default_factory=MimirIngestConfig,
        description="Entity detection settings for ingest.",
    )
    reflex: MimirReflexConfig = Field(
        default_factory=MimirReflexConfig,
        description="Retrieval reflex — deterministic entity pointer injection per turn.",
    )


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
    redis_url_env: str = Field(
        default="REDIS_URL",
        description="Environment variable containing the credential-bearing Redis URL.",
    )


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
    session_summary_max_tokens: int = Field(
        default=400,
        description="Maximum tokens for the proto-RWKV rolling session summary.",
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


class WakefulnessConfig(BaseModel):
    """Wakefulness trigger configuration (NIU-565).

    Controls the background trigger that detects operator silence, runs a
    cheap LLM reflection on the conversation, and emits 0–N follow-up
    intents as threads into Mímir via ``create_thread()``.

    Disabled by default — enable via ``wakefulness.enabled: true`` in the
    deployment YAML.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Enable the wakefulness trigger.  Off until explicitly activated; "
            "flip to true in the deployment ravn.yaml."
        ),
    )
    silence_threshold_seconds: int = Field(
        default=1800,
        description="Seconds of operator silence before a reflection fires (30 min).",
    )
    reflection_cooldown_seconds: int = Field(
        default=3600,
        description="Minimum seconds between shallow reflections (1 h).",
    )
    deep_reflection_threshold_seconds: int = Field(
        default=7200,
        description="Seconds of silence before a deep reflection fires (2 h).",
    )
    deep_reflection_cooldown_seconds: int = Field(
        default=14400,
        description="Minimum seconds between deep reflections (4 h).",
    )
    llm_alias: str = Field(
        default="fast",
        description=(
            "LLM alias for the reflection call.  Should resolve to a cheap/fast "
            "model in the LLM aliases map. Use empty, 'default', 'agent', or "
            "'same-as-agent' to reuse the effective agent model."
        ),
    )
    max_intents_per_reflection: int = Field(
        default=5,
        description="Cap on follow-up intents emitted per reflection.",
    )
    initial_thread_weight: float = Field(
        default=5.0,
        description="Weight assigned to newly created wakefulness threads.",
    )
    poll_interval_seconds: int = Field(
        default=60,
        description="How often (seconds) the trigger checks for silence.",
    )


# ---------------------------------------------------------------------------
# NIU-569: Recap trigger config
# ---------------------------------------------------------------------------


class PostSessionReflectionConfig(BaseModel):
    """Post-session reflection configuration (NIU-588).

    Controls the service that writes operational learnings to Mímir after
    each ``ravn.session.ended`` event.

    Disabled by default — enable via ``reflection.enabled: true`` in the
    deployment YAML.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Enable post-session reflection.  Off until explicitly activated; "
            "flip to true in the deployment ravn.yaml."
        ),
    )
    llm_alias: str = Field(
        default="fast",
        description=(
            "LLM alias for the reflection call.  Should resolve to a cheap/fast "
            "model in the LLM aliases map."
        ),
    )
    max_tokens: int = Field(
        default=1024,
        description="Maximum tokens the reflection LLM call may produce.",
    )
    learning_token_budget: int = Field(
        default=500,
        description=("Maximum tokens of injected learnings in the session-start system prompt."),
    )
    max_learnings_injected: int = Field(
        default=5,
        description="Maximum number of learning pages injected at session start.",
    )
    candidate_min_repetitions: int = Field(
        default=3,
        ge=2,
        description=(
            "Distinct session observations required before a reflection candidate "
            "may be promoted to a reusable learning without stronger evidence."
        ),
    )


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


# ---------------------------------------------------------------------------
# NIU-587: Dream cycle trigger — nightly Mímir enrichment, lint, cross-reference
# ---------------------------------------------------------------------------


class DreamCycleTriggerConfig(BaseModel):
    """Dream cycle trigger configuration (NIU-587).

    Fires the ``mimir-curator`` persona on a cron schedule to run a nightly
    enrichment pass over the Mímir knowledge base:

    1. Query Mímir log entries since the last dream cycle.
    2. Detect entities in new/modified raw sources.
    3. Update compiled truth pages where evidence has changed.
    4. Run ``mimir_lint --fix`` to auto-fix safe issues.
    5. Cross-reference pages that mention the same entities without links.
    6. Return summary counts; the drive loop emits ``mimir.dream.completed``.

    Disabled by default — enable via ``dream_cycle.enabled: true`` in ravn.yaml.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Enable the dream cycle trigger.  Off until explicitly activated; "
            "flip to true in the deployment ravn.yaml."
        ),
    )
    cron_expression: str = Field(
        default="0 3 * * *",
        description=(
            "Cron expression controlling when the dream cycle fires "
            "(default: 3 am daily).  Supports standard 5-field cron syntax."
        ),
    )
    persona: str = Field(
        default="mimir-curator",
        description="Persona used when running the dream cycle agent task.",
    )
    task_description: str = Field(
        default="Nightly dream cycle: enrich, lint, cross-reference",
        description="Human-readable title for the enqueued agent task.",
    )
    token_budget_usd: float = Field(
        default=0.50,
        description=(
            "Approximate USD token budget for the dream cycle run.  "
            "The agent is instructed to stay within this budget."
        ),
    )
    poll_interval_seconds: int = Field(
        default=60,
        description="How often (seconds) the trigger polls the cron schedule.",
    )
    state_dir: str = Field(
        default="~/.ravn/daemon",
        description="Directory where dream cycle state (last_run timestamp) is persisted.",
    )
    autonomy_mode: Literal["guarded", "autonomous", "yolo"] = Field(
        default="guarded",
        description=(
            "Autonomy mode for self-improvement proposals emitted by the "
            "Mimir-curator dream cycle trigger."
        ),
    )
    environment_id: str = Field(
        default="",
        description="Optional Environment ID attached to dream-cycle improvement proposals.",
    )
    proposal_store_path: str = Field(
        default="~/.ravn/autonomy_proposals.json",
        description="JSON proposal store used for dream-cycle self-improvement audit trails.",
    )


class OdinCourtConfig(BaseModel):
    """ODIN court resolver for resident judgments (NIU-1021).

    Runs inside resident daemons next to the learning runtime, resolving
    ``valkyrie.judgment.proposed``/``valkyrie.action.proposed`` into final
    attention and action decisions with persisted audit records.
    """

    enabled: bool = Field(
        default=True,
        description="Run the ODIN court when the resident environment is active.",
    )
    quorum_size: int = Field(
        default=1,
        description=(
            "Judgments/actions required before a case resolves immediately. "
            "Single-resident environments should keep 1; flocked deployments "
            "with multiple judging residents can raise it."
        ),
    )
    timeout_seconds: float = Field(
        default=30.0,
        description="Age after which a case with any input resolves on sweep.",
    )
    sweep_interval_seconds: float = Field(
        default=5.0,
        description="How often the court sweeps for timed-out cases.",
    )


class ResidentWakefulnessConfig(BaseModel):
    """Resident wakefulness state machine and scheduled consolidation dreams.

    Drives ``watching``/``wakeful``/``dreaming`` transitions for resident
    Valkyrie daemons and runs the reflective consolidation dream (skill
    telemetry review, stale marking, promotion, gap reopening) on a schedule.
    """

    enabled: bool = Field(
        default=False,
        description="Enable the wakefulness state machine for resident daemons.",
    )
    tick_interval_seconds: float = Field(
        default=5.0,
        description="How often the state machine evaluates transitions.",
    )
    wakeful_window_seconds: float = Field(
        default=30.0,
        description="Recency window of signal activity that keeps the resident wakeful.",
    )
    dream_interval_seconds: float = Field(
        default=3600.0,
        description="Seconds between scheduled consolidation dreams.",
    )
    dream_min_idle_seconds: float = Field(
        default=60.0,
        description="Minimum idle time before a due dream may start (no mid-incident dreams).",
    )
    stale_skill_age_seconds: float = Field(
        default=7 * 24 * 3600.0,
        description="Unused-for-this-long skills are marked stale during dreams.",
    )
    promote_min_successes: int = Field(
        default=3,
        description=(
            "Successful runs (with zero failures) before a private skill is "
            "promoted to environment scope during a dream, policy permitting."
        ),
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


class TrustLevelAutonomyTable(BaseModel):
    """Thresholds mapping a realm build-grant trust level to an autonomy mode.

    A grant ``level >= yolo`` resolves to ``yolo``, ``level >= autonomous`` to
    ``autonomous``, anything below to ``guarded``. Defaults mirror the rungs
    that used to live as constants in ``ravn.adapters.realm.client``.
    """

    autonomous: int = Field(
        default=2,
        description="Lowest trust level that resolves to the 'autonomous' mode.",
    )
    yolo: int = Field(
        default=4,
        description="Lowest trust level that resolves to the 'yolo' mode.",
    )

    @model_validator(mode="after")
    def _yolo_at_least_autonomous(self) -> TrustLevelAutonomyTable:
        if self.yolo < self.autonomous:
            msg = (
                "trust_level_autonomy_table.yolo "
                f"({self.yolo}) must be >= trust_level_autonomy_table.autonomous "
                f"({self.autonomous})"
            )
            raise ValueError(msg)
        return self


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


class ResidentEvolutionConfig(BaseModel):
    """Resident Valkyrie self-evolution: builder, reviewer, rollback, autonomy.

    Lives apart from :class:`DreamCycleTriggerConfig` (the Mimir-curator cron)
    — these settings drive the resident micro-dream/adoption loop, not the
    nightly knowledge-base pass.
    """

    autonomy_mode: Literal["guarded", "autonomous", "yolo"] = Field(
        default="guarded",
        description=(
            "Resident autonomy: guarded records proposals, autonomous applies "
            "low-risk private/Environment changes, yolo evolves within "
            "delegated boundaries."
        ),
    )
    reviewer_adapter: str = Field(
        default="ravn.valkyrie_evolution.adapters.PolicyCourtReviewer",
        description=(
            "Fully-qualified EvolutionReviewPort class gating built and "
            "adopted learnings before install."
        ),
    )
    reviewer_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Constructor kwargs for the reviewer adapter.",
    )
    tool_timeout_seconds: float = Field(
        default=10.0,
        description="Hard timeout for one sandboxed resident tool execution.",
    )
    rollback_consecutive_failures: int = Field(
        default=3,
        description=(
            "Consecutive implementation failures before an installed skill is "
            "auto-rolled-back (archived, regression published to the flock)."
        ),
    )
    feedback_confidence_bump: float = Field(
        default=0.05,
        description=(
            "How much one useful/good_action operator feedback verdict raises "
            "the stored learning's confidence (clamped at 1.0). Feedback "
            "arrives on the same odin.review.decided command channel as every "
            "other operator learning decision."
        ),
    )
    skill_inventory_interval_seconds: float = Field(
        default=300.0,
        description=(
            "How often the resident republishes its full learned-skill "
            "inventory as valkyrie.evolution.skill_inventory events. The "
            "dashboard's skill mirror runs with zero telemetry replay, so this "
            "heartbeat (plus a snapshot at startup and after every adoption / "
            "rollback) is what keeps the mirror populated with the skills a "
            "resident actively uses. 0 disables the heartbeat (startup and "
            "opportunistic snapshots still fire)."
        ),
    )
    learned_tool_execution_backend: Literal[
        "container", "local", "forge", "devrunner", "k8s_job"
    ] = Field(
        default="container",
        description=(
            "Execution backend for learned agent tools authored through build_tool. "
            "'container' runs each invocation in a fail-closed, least-reach OCI "
            "container; 'local' is an explicit compatibility mode without hard "
            "reach enforcement; 'k8s_job' creates one locked-down Job per invocation "
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
            "ravn.adapters.tool_build.TingWorkflowToolBuildBackend). Empty: the "
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
            "with a Ting workflow build backend, the backend discovers the "
            "matching workflow from the catalog instead of requiring a "
            "hardcoded workflow_id."
        ),
    )
    realm_slug: str = Field(
        default="",
        description=(
            "This Valkyrie's realm slug. When set, the resident resolves its "
            "tool-build workflow and autonomy from the realm's 'build' trust "
            "grant (via the niuu realm governance API on the Volundr host). "
            "Empty keeps today's static tool_builder_workflow / autonomy_mode "
            "behavior."
        ),
    )
    realm_api_base_url: str = Field(
        default="",
        description=(
            "Base URL of the realm governance API (the Volundr host that also "
            "serves Forge sessions). Empty derives it from "
            "tool_build_kwargs['base_url']."
        ),
    )
    realm_api_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Auth passthrough for realm API calls (external_token_env, "
            "workload_token_file, workload_exchange_url, workload_audiences — "
            "the same keys client_from_workload_identity accepts). Empty reuses "
            "the tool_build_kwargs auth settings."
        ),
    )
    build_repair_attempts: int = Field(
        default=3,
        description=(
            "Maximum verify/repair attempts for a commissioned or inline-built "
            "tool before the build aborts without installing."
        ),
    )
    self_registered_tool_confidence: float = Field(
        default=0.74,
        description=(
            "Confidence a self-built tool travels to the flock with — used for "
            "both the flock learning proposal and the resident artifact."
        ),
    )
    trust_level_autonomy_table: TrustLevelAutonomyTable = Field(
        default_factory=TrustLevelAutonomyTable,
        description=(
            "Thresholds mapping a realm build-grant trust level to an autonomy "
            "mode: level >= yolo -> yolo, >= autonomous -> autonomous, else "
            "guarded."
        ),
    )
    review_attention_tiers: list[str] = Field(
        default_factory=lambda: ["present", "urgent"],
        description=(
            "Judgment attention tiers that mean 'a human should look at this'. "
            "Judgments pitched below these tiers stay telemetry and never "
            "reach the operator inbox."
        ),
    )
    observational_actions: list[str] = Field(
        default_factory=lambda: ["", "none", "n/a", "watch", "observe"],
        description=(
            "Recommended actions that describe observation, not an action to "
            "approve — judgments recommending only these never reach the "
            "operator inbox."
        ),
    )

    @model_validator(mode="after")
    def _validate_k8s_job_contract(self) -> ResidentEvolutionConfig:
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


class ResidentInboxConfig(BaseModel):
    """Resident inbox intake and triage configuration."""

    enabled: bool = Field(
        default=True,
        description="Persist and triage resident inbox signals when resident autonomy is enabled.",
    )
    adapter: str = Field(
        default="ravn.resident_inbox.backend.LocalResidentInbox",
        description="Fully-qualified ResidentInboxBackend adapter class.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Constructor kwargs passed to the resident inbox adapter.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Maps resident inbox adapter kwargs to secret environment variables.",
    )
    environment_signals_enabled: bool = Field(
        default=False,
        description=(
            "Persist configured Environment signals through the resident inbox adapter. "
            "Disabled by default so existing deployments opt into this delivery owner explicitly."
        ),
    )
    directed_messages_enabled: bool = Field(
        default=True,
        description=(
            "Record generic Skuld directed messages as resident inbox signals without "
            "consuming normal steering or task enqueue behavior."
        ),
    )
    max_signals_per_wake: int = Field(
        default=5,
        description="Maximum new resident inbox signals triaged in one wake pass.",
    )
    create_objectives: bool = Field(
        default=True,
        description="Allow inbox triage to create resident objectives for actionable signals.",
    )
    attach_to_existing_objectives: bool = Field(
        default=True,
        description="Allow inbox triage to attach signals to matching resident objectives.",
    )
    min_attach_score: int = Field(
        default=2,
        description="Minimum keyword overlap required before attaching to an existing objective.",
    )
    signal_retention_max_pages: int = Field(
        default=500,
        description=(
            "Target maximum resident inbox signal records kept by supporting adapters. "
            "Only processed records are eligible, so an unconsumed backlog may exceed the cap. "
            "The inbox is a "
            "rolling working set, not an archive — signal pages are write-only "
            "records that otherwise accumulate without bound and poison "
            "backing-store queries. Oldest records beyond the cap are pruned. 0 disables "
            "count-based pruning."
        ),
    )
    signal_retention_max_age_days: float = Field(
        default=7.0,
        description=(
            "Maximum age in days of resident inbox signal pages; older pages "
            "are pruned. 0 disables age-based pruning."
        ),
    )
    signal_retention_sweep_interval_seconds: float = Field(
        default=900.0,
        description=(
            "Minimum seconds between inbox retention sweeps. Sweeps run off "
            "the signal write path in a worker thread; this throttle bounds "
            "how often the signals directory is rescanned."
        ),
    )


class ResidentStateConfig(BaseModel):
    """Resident memory/state adapter selection.

    ``adapter`` is the preferred store (GBrain by default); ``fallback_adapter``
    is used only when the preferred adapter reports it is not available (e.g.
    GBrain's CLI/endpoint is absent). Selection is done by
    ``ravn.adapters.resident_state.select_resident_state`` — no caller branches
    on adapter type.
    """

    adapter: str = Field(
        default="ravn.adapters.resident_state.gbrain.GBrainResidentStateAdapter",
        description="Fully-qualified preferred ResidentStatePort adapter class.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Constructor kwargs passed to the preferred resident state adapter.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Maps adapter kwarg names to env var names for secret injection.",
    )
    fallback_adapter: str = Field(
        default="ravn.adapters.resident_state.mimir.LocalResidentState",
        description="Adapter used when the preferred adapter is unavailable.",
    )
    fallback_kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Constructor kwargs passed to the fallback resident state adapter.",
    )
    fallback_secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Maps fallback adapter kwarg names to env var names for secret injection.",
    )
    continuation_max_turns: int = Field(
        default=3,
        ge=1,
        description="Maximum model turns in one automatically continued resident case.",
    )
    continuation_max_tokens: int = Field(
        default=0,
        ge=0,
        description="Cumulative resident-case token cap; 0 leaves the token cap disabled.",
    )
    continuation_context_max_chars: int = Field(
        default=12000,
        ge=1000,
        description="Maximum durable prior-turn characters restored into a continuation.",
    )
    continuation_tool_result_max_chars: int = Field(
        default=2000,
        ge=100,
        description="Maximum characters persisted from each resident tool result.",
    )
    directed_message_context_max_chars: int = Field(
        default=4000,
        ge=200,
        description=(
            "Maximum characters of prior room content carried into a directed-message "
            "task. The human's current message is never truncated by this setting."
        ),
    )
    home_wake_interval_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Seconds between routine resident home turns over the durable inbox.",
    )
    scheduled_wake_default_seconds: float = Field(
        default=3600.0,
        gt=0,
        description=(
            "Delay applied when a resident turn sleeps for a scheduled time without "
            "naming an explicit wake_at timestamp."
        ),
    )
    stewardship_interval_seconds: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Seconds of resident quiet after which a charter-driven stewardship turn "
            "runs even though no new observation arrived. 0 disables stewardship wakes, "
            "leaving the resident purely signal-driven."
        ),
    )


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


class SignalSourceConfig(BaseModel):
    """Adapter-backed signal source for a resident Valkyrie Environment."""

    id: str = Field(
        default="",
        description="Stable source id, for example k8s-events or host-mail.",
    )
    name: str = Field(
        default="",
        description="Human-readable source name for UI and audit logs.",
    )
    adapter: str = Field(
        default="",
        description="Import path or connector id for the signal source adapter.",
    )
    kind: str = Field(
        default="generic",
        description="Provider-neutral signal kind, for example events, metrics, or messages.",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this source should be started by the resident Valkyrie.",
    )
    kwargs: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter constructor kwargs. Keep mesh transport subjects out of this shape.",
    )
    secret_kwargs_env: dict[str, str] = Field(
        default_factory=dict,
        description="Map adapter kwarg names to environment variable names for secrets.",
    )


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
    resident_personality: str = Field(
        default="",
        description=(
            "Optional lightweight resident guidance injected into autonomous Valkyrie tasks."
        ),
    )
    charter: str = Field(
        default="",
        description=(
            "The human seed for this resident: a few sentences describing what the "
            "Valkyrie stewards and what 'better' means for its environment. Injected "
            "into every autonomous task and surfaced on the dashboard."
        ),
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
    signal_sources: list[SignalSourceConfig] = Field(
        default_factory=list,
        description="Adapter-backed signal feeds this Valkyrie should watch.",
    )
    capability_sources: list[CapabilitySourceConfig] = Field(
        default_factory=list,
        description=(
            "Dynamic adapters that discover remote workflow capabilities from existing catalogs."
        ),
    )
    signal_poll_interval_seconds: float = Field(
        default=10.0,
        description="Seconds between polling enabled signal sources.",
    )
    signal_dedupe_cache_size: int = Field(
        default=2048,
        description="Maximum signal ids remembered per daemon to avoid duplicate work.",
    )
    signal_task_severities: list[str] = Field(
        default_factory=lambda: ["warning", "critical"],
        description="Signal severities that should enqueue autonomous Valkyrie tasks.",
    )
    idle_triage_interval_seconds: float = Field(
        default=900.0,
        description=(
            "Seconds between idle triage judgments over signals that did not match "
            "signal_task_severities. 0 disables idle triage."
        ),
    )
    idle_triage_max_signals: int = Field(
        default=200,
        description="Maximum below-threshold signals summarized in one idle triage task.",
    )
    idle_triage_sample_signals: int = Field(
        default=15,
        description=(
            "Maximum individual signal lines rendered in an idle triage prompt; "
            "the rest of the batch is summarized by the severity breakdown and "
            "an explicit overflow line so the prompt stays bounded."
        ),
    )
    idle_triage_sample_summary_max_chars: int = Field(
        default=300,
        description="Maximum characters of one signal summary rendered in an idle triage prompt.",
    )
    idle_triage_max_signal_refs: int = Field(
        default=25,
        description="Maximum signal refs listed in the idle triage outcome template.",
    )
    signal_subjects: list[str] = Field(
        default_factory=list,
        description=(
            "Legacy derived bus subjects. Prefer signal_sources in user config; "
            "transport adapters derive subscriptions from enabled sources."
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


class WardenDiscoveryAdapterConfig(BaseModel):
    """Dynamic adapter entry for Warden discovery."""

    adapter: str = Field(
        default="ravn.adapters.warden_discovery.spec.WardenSpecDiscoveryAdapter",
        description="Fully-qualified class path for the Warden discovery adapter.",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "allow"}

    def adapter_kwargs(self) -> dict[str, Any]:
        """Return constructor kwargs from explicit kwargs plus extra fields."""
        extras = self.__pydantic_extra__ or {}
        return {**self.kwargs, **extras}


class WardenDiscoveryConfig(BaseModel):
    """Read-only Warden discovery configuration."""

    enabled: bool = True
    adapters: list[WardenDiscoveryAdapterConfig] = Field(
        default_factory=lambda: [
            WardenDiscoveryAdapterConfig(
                adapter="ravn.adapters.warden_discovery.spec.WardenSpecDiscoveryAdapter"
            )
        ]
    )
    adapters_json: str = Field(
        default="",
        description="JSON list of adapter objects for env-driven deployments.",
    )

    @model_validator(mode="after")
    def _parse_adapters_json(self) -> WardenDiscoveryConfig:
        if not self.adapters_json.strip():
            return self
        raw_adapters = json.loads(self.adapters_json)
        if not isinstance(raw_adapters, list):
            msg = "warden_discovery.adapters_json must be a JSON list"
            raise ValueError(msg)
        self.adapters = [WardenDiscoveryAdapterConfig.model_validate(item) for item in raw_adapters]
        return self


class ResidentDiscoveryAdapterConfig(BaseModel):
    """Dynamic adapter entry for standalone-resident discovery."""

    adapter: str = Field(
        default=("ravn.adapters.resident_discovery.kubernetes.KubernetesResidentDiscoveryAdapter"),
        description="Fully-qualified class path for the resident discovery adapter.",
    )
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "allow"}

    def adapter_kwargs(self) -> dict[str, Any]:
        """Return constructor kwargs from explicit kwargs plus extra fields."""
        extras = self.__pydantic_extra__ or {}
        return {**self.kwargs, **extras}


class ResidentDiscoveryConfig(BaseModel):
    """Read-only standalone-resident discovery configuration.

    No adapters are configured by default: discovering residents from a
    cluster is an explicit deployment decision (in-cluster RBAC, or a local
    kubeconfig that may point at a live cluster).
    """

    enabled: bool = True
    adapters: list[ResidentDiscoveryAdapterConfig] = Field(default_factory=list)
    adapters_json: str = Field(
        default="",
        description="JSON list of adapter objects for env-driven deployments.",
    )

    @model_validator(mode="after")
    def _parse_adapters_json(self) -> ResidentDiscoveryConfig:
        if not self.adapters_json.strip():
            return self
        raw_adapters = json.loads(self.adapters_json)
        if not isinstance(raw_adapters, list):
            msg = "resident_discovery.adapters_json must be a JSON list"
            raise ValueError(msg)
        self.adapters = [
            ResidentDiscoveryAdapterConfig.model_validate(item) for item in raw_adapters
        ]
        return self


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


class _ValkyrieTelemetryEnvSource(EnvSettingsSource):
    """Ignore generic NATS_URL while preserving explicit telemetry aliases."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self.env_vars.pop("nats_url", None)


class ValkyrieTelemetryConfig(_LegacyAliasSettings):
    """Typed transport settings for Valkyrie dashboard telemetry."""

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
        del cls, env_settings, dotenv_settings
        return (
            _ValkyrieTelemetryEnvSource(settings_cls),
            init_settings,
            file_secret_settings,
        )

    nats_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nats_url",
            "RAVN_VALKYRIE_TELEMETRY_NATS_URL",
        ),
    )
    nats_streams: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nats_streams",
            "RAVN_VALKYRIE_TELEMETRY_NATS_STREAMS",
        ),
    )
    nats_stream: str = Field(
        default="ravn_environment",
        validation_alias=AliasChoices(
            "nats_stream",
            "RAVN_VALKYRIE_TELEMETRY_NATS_STREAM",
        ),
    )
    subject_prefix: str = Field(
        default="ravn.environment",
        validation_alias=AliasChoices(
            "subject_prefix",
            "RAVN_VALKYRIE_TELEMETRY_SUBJECT_PREFIX",
        ),
    )
    nats_user: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nats_user",
            "RAVN_VALKYRIE_TELEMETRY_NATS_USER",
        ),
    )
    nats_password_env: str = Field(default="RAVN_VALKYRIE_TELEMETRY_NATS_PASSWORD")
    nats_token_env: str = Field(default="RAVN_VALKYRIE_TELEMETRY_NATS_TOKEN")
    nkeys_seed_env: str = Field(default="RAVN_VALKYRIE_TELEMETRY_NKEYS_SEED")
    nkeys_seed_file: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nkeys_seed_file",
            "RAVN_VALKYRIE_TELEMETRY_NKEYS_SEED_FILE",
        ),
    )
    consumer_group: str = Field(
        default="ravn-valkyrie-dashboard",
        validation_alias=AliasChoices(
            "consumer_group",
            "RAVN_VALKYRIE_TELEMETRY_CONSUMER_GROUP",
        ),
    )
    replay_seconds: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices(
            "replay_seconds",
            "RAVN_VALKYRIE_TELEMETRY_REPLAY_SECONDS",
        ),
    )
    retry_seconds: int = Field(
        default=30,
        gt=0,
        validation_alias=AliasChoices(
            "retry_seconds",
            "RAVN_VALKYRIE_TELEMETRY_RETRY_SECONDS",
        ),
    )
    startup_delay_seconds: float = Field(
        default=5.0,
        ge=0,
        validation_alias=AliasChoices(
            "startup_delay_seconds",
            "RAVN_VALKYRIE_TELEMETRY_STARTUP_DELAY_SECONDS",
        ),
    )
    start_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        validation_alias=AliasChoices(
            "start_timeout_seconds",
            "RAVN_VALKYRIE_TELEMETRY_START_TIMEOUT_SECONDS",
        ),
    )
    connect_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        validation_alias=AliasChoices(
            "connect_timeout_seconds",
            "RAVN_VALKYRIE_TELEMETRY_NATS_CONNECT_TIMEOUT_SECONDS",
        ),
    )
    nats_jetstream_domain: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nats_jetstream_domain",
            "RAVN_VALKYRIE_TELEMETRY_NATS_JETSTREAM_DOMAIN",
        ),
    )
    nats_max_reconnect_attempts: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices(
            "nats_max_reconnect_attempts",
            "RAVN_VALKYRIE_TELEMETRY_NATS_MAX_RECONNECT_ATTEMPTS",
        ),
    )
    tls_ca_file: str = Field(
        default="",
        validation_alias=AliasChoices(
            "tls_ca_file",
            "RAVN_VALKYRIE_TELEMETRY_TLS_CA_FILE",
        ),
    )
    tls_cert_file: str = Field(
        default="",
        validation_alias=AliasChoices(
            "tls_cert_file",
            "RAVN_VALKYRIE_TELEMETRY_TLS_CERT_FILE",
        ),
    )
    tls_key_file: str = Field(
        default="",
        validation_alias=AliasChoices(
            "tls_key_file",
            "RAVN_VALKYRIE_TELEMETRY_TLS_KEY_FILE",
        ),
    )
    tls_hostname: str = Field(
        default="",
        validation_alias=AliasChoices(
            "tls_hostname",
            "RAVN_VALKYRIE_TELEMETRY_TLS_HOSTNAME",
        ),
    )
    tls_handshake_first: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "tls_handshake_first",
            "RAVN_VALKYRIE_TELEMETRY_TLS_HANDSHAKE_FIRST",
        ),
    )
    tls_insecure_skip_verify: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "tls_insecure_skip_verify",
            "RAVN_VALKYRIE_TELEMETRY_TLS_INSECURE_SKIP_VERIFY",
        ),
    )


class ValkyrieCommandConfig(_LegacyAliasSettings):
    """Typed transport settings for Valkyrie review commands."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    nats_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nats_url",
            "RAVN_VALKYRIE_COMMAND_NATS_URL",
        ),
    )
    nats_streams: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nats_streams",
            "RAVN_VALKYRIE_COMMAND_NATS_STREAMS",
        ),
    )
    nats_stream: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nats_stream",
            "RAVN_VALKYRIE_COMMAND_NATS_STREAM",
        ),
    )
    subject_prefix: str = Field(
        default="",
        validation_alias=AliasChoices(
            "subject_prefix",
            "RAVN_VALKYRIE_COMMAND_SUBJECT_PREFIX",
        ),
    )
    nats_user: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nats_user",
            "RAVN_VALKYRIE_COMMAND_NATS_USER",
        ),
    )
    nats_password_env: str = Field(default="RAVN_VALKYRIE_COMMAND_NATS_PASSWORD")
    nats_token_env: str = Field(default="RAVN_VALKYRIE_COMMAND_NATS_TOKEN")
    nkeys_seed_env: str = Field(default="RAVN_VALKYRIE_COMMAND_NKEYS_SEED")
    nkeys_seed_file: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nkeys_seed_file",
            "RAVN_VALKYRIE_COMMAND_NKEYS_SEED_FILE",
        ),
    )
    nats_jetstream_domain: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nats_jetstream_domain",
            "RAVN_VALKYRIE_COMMAND_NATS_JETSTREAM_DOMAIN",
        ),
    )
    ensure_stream: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ensure_stream",
            "RAVN_VALKYRIE_COMMAND_NATS_ENSURE_STREAM",
        ),
    )
    connect_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        validation_alias=AliasChoices(
            "connect_timeout_seconds",
            "RAVN_VALKYRIE_COMMAND_NATS_CONNECT_TIMEOUT_SECONDS",
        ),
    )
    max_reconnect_attempts: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices(
            "max_reconnect_attempts",
            "RAVN_VALKYRIE_COMMAND_NATS_MAX_RECONNECT_ATTEMPTS",
        ),
    )
    start_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        validation_alias=AliasChoices(
            "start_timeout_seconds",
            "RAVN_VALKYRIE_COMMAND_NATS_START_TIMEOUT_SECONDS",
        ),
    )
    tls_ca_file: str = Field(
        default="",
        validation_alias=AliasChoices(
            "tls_ca_file",
            "RAVN_VALKYRIE_COMMAND_TLS_CA_FILE",
        ),
    )
    tls_cert_file: str = Field(
        default="",
        validation_alias=AliasChoices(
            "tls_cert_file",
            "RAVN_VALKYRIE_COMMAND_TLS_CERT_FILE",
        ),
    )
    tls_key_file: str = Field(
        default="",
        validation_alias=AliasChoices(
            "tls_key_file",
            "RAVN_VALKYRIE_COMMAND_TLS_KEY_FILE",
        ),
    )
    tls_hostname: str = Field(
        default="",
        validation_alias=AliasChoices(
            "tls_hostname",
            "RAVN_VALKYRIE_COMMAND_TLS_HOSTNAME",
        ),
    )
    tls_handshake_first: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "tls_handshake_first",
            "RAVN_VALKYRIE_COMMAND_TLS_HANDSHAKE_FIRST",
        ),
    )
    tls_insecure_skip_verify: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "tls_insecure_skip_verify",
            "RAVN_VALKYRIE_COMMAND_TLS_INSECURE_SKIP_VERIFY",
        ),
    )


class ValkyrieRoomConfig(_LegacyAliasSettings):
    """Typed Skuld room bridge settings."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "url",
            "RAVN_VALKYRIE_SKULD_ROOM_URL",
        ),
    )
    timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        validation_alias=AliasChoices(
            "timeout_seconds",
            "RAVN_VALKYRIE_SKULD_ROOM_TIMEOUT_SECONDS",
        ),
    )


class OdinReviewConfig(_LegacyAliasSettings):
    """Durable ODIN review queue and expiry policy."""

    database_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "database_url",
            "RAVN_ODIN_REVIEW_DATABASE_URL",
        ),
    )
    store_path: str = Field(
        default="~/.ravn/odin_review_queue.json",
        validation_alias=AliasChoices(
            "store_path",
            "RAVN_ODIN_REVIEW_STORE_PATH",
        ),
    )
    default_ttl_seconds: float = Field(
        default=0.0,
        ge=0,
        validation_alias=AliasChoices(
            "default_ttl_seconds", "RAVN_ODIN_REVIEW_DEFAULT_TTL_SECONDS"
        ),
    )
    evolution_build_ttl_seconds: float | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices(
            "evolution_build_ttl_seconds",
            "RAVN_ODIN_REVIEW_TTL_SECONDS_EVOLUTION_BUILD",
        ),
    )
    skill_promotion_ttl_seconds: float | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices(
            "skill_promotion_ttl_seconds",
            "RAVN_ODIN_REVIEW_TTL_SECONDS_SKILL_PROMOTION",
        ),
    )
    flock_learning_ttl_seconds: float | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices(
            "flock_learning_ttl_seconds",
            "RAVN_ODIN_REVIEW_TTL_SECONDS_FLOCK_LEARNING",
        ),
    )
    court_escalation_ttl_seconds: float | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices(
            "court_escalation_ttl_seconds",
            "RAVN_ODIN_REVIEW_TTL_SECONDS_COURT_ESCALATION",
        ),
    )
    autonomy_change_ttl_seconds: float | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices(
            "autonomy_change_ttl_seconds",
            "RAVN_ODIN_REVIEW_TTL_SECONDS_AUTONOMY_CHANGE",
        ),
    )
    morning_brief_ttl_seconds: float | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices(
            "morning_brief_ttl_seconds",
            "RAVN_ODIN_REVIEW_TTL_SECONDS_MORNING_BRIEF",
        ),
    )
    sweep_interval_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias=AliasChoices(
            "sweep_interval_seconds", "RAVN_ODIN_REVIEW_SWEEP_INTERVAL_SECONDS"
        ),
    )

    def ttl_seconds_by_kind(self) -> dict[str, float]:
        """Return only per-kind TTL overrides explicitly configured."""
        values: dict[str, float] = {}
        for kind in (
            "evolution_build",
            "skill_promotion",
            "flock_learning",
            "court_escalation",
            "autonomy_change",
            "morning_brief",
        ):
            value = getattr(self, f"{kind}_ttl_seconds")
            if value is not None:
                values[kind] = value
        return values


class ValkyrieRuntimeConfig(_LegacyAliasSettings):
    """Valkyrie API runtime behavior, loaded through Ravn settings."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    telemetry: ValkyrieTelemetryConfig = Field(default_factory=ValkyrieTelemetryConfig)
    command: ValkyrieCommandConfig = Field(default_factory=ValkyrieCommandConfig)
    room: ValkyrieRoomConfig = Field(default_factory=ValkyrieRoomConfig)
    odin_reviews: OdinReviewConfig = Field(default_factory=OdinReviewConfig)
    brief_interval_seconds: float = Field(
        default=86_400.0,
        ge=0,
        validation_alias=AliasChoices(
            "brief_interval_seconds",
            "RAVN_VALKYRIE_BRIEF_INTERVAL_SECONDS",
        ),
    )


class Settings(BaseSettings):
    """Ravn application settings.

    Loaded from YAML with RAVN_ environment variable overrides.
    Precedence: env vars > yaml file > defaults.
    """

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
    skill: SkillConfig = Field(default_factory=SkillConfig)

    # NIU-501: self-improvement loop
    evolution: EvolutionConfig = Field(default_factory=EvolutionConfig)

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

    # NIU-558: thread enrichment queue
    thread: ThreadConfig = Field(default_factory=ThreadConfig)

    # NIU-565: wakefulness trigger
    wakefulness: WakefulnessConfig = Field(default_factory=WakefulnessConfig)

    # NIU-569: recap trigger
    recap: RecapConfig = Field(default_factory=RecapConfig)

    # NIU-587: dream cycle trigger — nightly Mímir enrichment
    dream_cycle: DreamCycleTriggerConfig = Field(default_factory=DreamCycleTriggerConfig)

    # NIU-1040: resident wakefulness state machine + scheduled consolidation dreams
    resident_wakefulness: ResidentWakefulnessConfig = Field(
        default_factory=ResidentWakefulnessConfig
    )

    # NIU-1021: ODIN court resolver for resident judgments
    odin_court: OdinCourtConfig = Field(default_factory=OdinCourtConfig)

    # Resident Valkyrie self-evolution loop (builder/reviewer/rollback)
    resident_evolution: ResidentEvolutionConfig = Field(default_factory=ResidentEvolutionConfig)

    resident_state: ResidentStateConfig = Field(default_factory=ResidentStateConfig)
    resident_inbox: ResidentInboxConfig = Field(default_factory=ResidentInboxConfig)

    # NIU-588: post-session reflection → Mímir learnings
    reflection: PostSessionReflectionConfig = Field(default_factory=PostSessionReflectionConfig)

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

    # Warden discovery for UI/Guild/Observatory surfaces.
    warden_discovery: WardenDiscoveryConfig = Field(default_factory=WardenDiscoveryConfig)

    # Standalone-resident discovery (helm-deployed residents outside Forge).
    resident_discovery: ResidentDiscoveryConfig = Field(default_factory=ResidentDiscoveryConfig)

    # NIU-435: cascade coordinator / flock delegation / ephemeral spawn
    cascade: CascadeConfig = Field(default_factory=CascadeConfig)

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
    valkyrie: ValkyrieRuntimeConfig = Field(
        default_factory=ValkyrieRuntimeConfig,
        description="Resident Valkyrie API transport and bridge configuration.",
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
    llm_adapter: LLMAdapterConfig = Field(default_factory=LLMAdapterConfig)
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

    def effective_post_session_reflection_config(self) -> PostSessionReflectionConfig:
        """Return post-session reflection config with any model fallback resolved."""

        if not self._uses_effective_agent_model(self.reflection.llm_alias):
            return self.reflection
        return self.reflection.model_copy(update={"llm_alias": self.effective_model()})

    def effective_max_tokens(self) -> int:
        """Return the resolved max_tokens.

        Same backward-compat logic as :meth:`effective_model`.
        """
        _default = 8192
        if self.llm.max_tokens != _default:
            return self.llm.max_tokens
        if self.agent.max_tokens != _default:
            import logging as _log

            _log.getLogger(__name__).warning(
                "agent.max_tokens is deprecated — use llm.max_tokens instead",
            )
            return self.agent.max_tokens
        return _default


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

_VALID_PERMISSION_MODES: frozenset[str] = frozenset(
    {
        "read_only",
        "workspace_write",
        "workspace-write",
        "full_access",
        "full-access",
        "prompt",
        # Legacy aliases
        "allow_all",
        "deny_all",
    }
)

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

        permission_mode = str(raw.get("permission_mode", ""))
        if permission_mode and permission_mode not in _VALID_PERMISSION_MODES:
            warnings.append(
                f"Unknown permission_mode {permission_mode!r}. "
                f"Valid values: {sorted(_VALID_PERMISSION_MODES)}"
            )

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
