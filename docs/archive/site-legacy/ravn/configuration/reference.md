# Configuration Reference

Complete reference for every Ravn configuration section. All fields show their
default values. Types use Python 3.12+ syntax.

## `anthropic`

Anthropic API connection settings. Typically set via environment variable.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `api_key` | str | `""` | API key. Prefer `ANTHROPIC_API_KEY` env var. |
| `base_url` | str | `"https://api.anthropic.com"` | API base URL. |

## `llm`

LLM provider configuration with fallback chain support.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | str | `"claude-sonnet-4-6"` | Model identifier or Bifrost alias. |
| `max_tokens` | int | `8192` | Max output tokens per LLM call. |
| `max_retries` | int | `3` | Retries on transient errors (429, 5xx). |
| `retry_base_delay` | float | `1.0` | Base delay (seconds) for exponential backoff. |
| `timeout` | float | `120.0` | Request timeout in seconds. |
| `provider` | [LLMProviderConfig](#llmproviderconfig) | see below | Primary LLM adapter. |
| `fallbacks` | list[[LLMProviderConfig](#llmproviderconfig)] | `[]` | Ordered fallback providers. |
| `extended_thinking` | [ExtendedThinkingConfig](#extended_thinking) | see below | Thinking mode settings. |

### `LLMProviderConfig`

Dynamic adapter specification for LLM providers.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `adapter` | str | `"ravn.adapters.llm.anthropic.AnthropicAdapter"` | Fully-qualified class path. |
| `kwargs` | dict | `{}` | Constructor arguments. |
| `secret_kwargs_env` | dict | `{}` | Env var names for secret constructor args. |

### `extended_thinking`

Extended thinking (Claude-only). Enables deeper reasoning on complex tasks.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Allow thinking activation. |
| `budget_tokens` | int | `8000` | Max reasoning tokens per activation. |
| `auto_trigger` | bool | `true` | Auto-activate on planning/ambiguous tasks. |
| `auto_trigger_on_retry` | bool | `true` | Auto-activate after first tool failure. |

## `agent`

Core agent behavior.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model` | str | `"claude-sonnet-4-6"` | Legacy model field (use `llm.model` instead). |
| `max_tokens` | int | `8192` | Legacy max tokens (use `llm.max_tokens`). |
| `max_iterations` | int | `20` | Max tool-call iterations per turn. |
| `system_prompt` | str | `"You are Ravn..."` | Base system prompt. |
| `episode_summary_max_chars` | int | `500` | Max chars of response stored as episode summary. |
| `episode_task_max_chars` | int | `200` | Max chars of user input stored as episode task. |
| `outcome` | [OutcomeConfig](#outcome) | see below | Task outcome and reflection settings. |

### `outcome`

Controls task outcome recording and reflection.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Record task outcomes. |
| `path` | str | `"~/.ravn/memory.db"` | Outcome storage path. |
| `reflection_model` | str | `"claude-haiku-4-5-20251001"` | Model for post-task reflection. |
| `reflection_max_tokens` | int | `512` | Max tokens for reflection output. |
| `lessons_limit` | int | `3` | Max lessons extracted per outcome. |
| `task_summary_max_chars` | int | `200` | Max chars for task summary. |
| `lessons_token_budget` | int | `1500` | Token budget for lessons in prefetch. |
| `input_token_cost_per_million` | float | `3.0` | Cost tracking: input token price. |
| `output_token_cost_per_million` | float | `15.0` | Cost tracking: output token price. |

## `context`

Project context discovery — how Ravn loads project files into system prompt.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `per_file_limit` | int | `4096` | Max characters from a single context file. |
| `total_budget` | int | `12288` | Max total context chars injected into system prompt. |

## `tools`

Tool availability, profiles, and sub-tool configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | list[str] | `[]` | Allowlist of built-in tool names (empty = all). |
| `disabled` | list[str] | `[]` | Blocklist of built-in tool names. |
| `custom` | list[ToolAdapterConfig] | `[]` | Custom tool adapters (dynamic import). |
| `profiles` | dict | `{}` | Named tool profiles. |
| `file` | [FileToolsConfig](#toolsfile) | see below | File tool limits. |
| `terminal` | [TerminalToolConfig](#toolsterminal) | see below | Terminal/shell settings. |
| `web` | [WebToolsConfig](#toolsweb) | see below | Web tool settings. |
| `bash` | [BashToolConfig](#toolsbash) | see below | Bash tool settings. |

### `tools.file`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_read_bytes` | int | `1048576` | Max file read size (1 MB). |
| `max_write_bytes` | int | `5242880` | Max file write size (5 MB). |
| `binary_check_bytes` | int | `8192` | Bytes checked for binary detection (8 KB). |

### `tools.terminal`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | str | `"local"` | `local` or `docker`. |
| `persistent_shell` | bool | `true` | Keep shell alive across tool calls. |
| `shell` | str | `"/bin/bash"` | Shell executable path. |
| `timeout_seconds` | float | `30.0` | Per-command timeout. |
| `docker` | DockerTerminalConfig | see below | Docker backend settings. |

**`docker` sub-config:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `image` | str | `"python:3.11-slim"` | Docker image. |
| `network` | str | `"none"` | Docker network mode. |
| `mount_workspace` | bool | `true` | Mount workspace into container. |
| `extra_mounts` | list[str] | `[]` | Additional volume mounts. |

### `tools.web`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `fetch.timeout` | float | `30.0` | Fetch timeout (seconds). |
| `fetch.user_agent` | str | `"Ravn/1.0 (...)"` | HTTP User-Agent header. |
| `fetch.content_budget` | int | `20000` | Max characters returned. |
| `search.provider` | ToolAdapterConfig | MockWebSearchProvider | Search adapter. |
| `search.num_results` | int | `5` | Results per search. |

### `tools.bash`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | str | `"workspace_write"` | Permission mode for bash commands. |
| `timeout_seconds` | float | `120.0` | Command timeout. |
| `max_output_bytes` | int | `102400` | Max stdout+stderr (100 KB). |
| `workspace_root` | str | `""` | Workspace root for path validation. |

## `permission`

Authorization enforcement.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | str | `"workspace_write"` | Default mode: `read_only`, `workspace_write`, `full_access`, `prompt`. |
| `workspace_root` | str | `""` | Absolute path for workspace boundary. |
| `allow` | list[str] | `[]` | Tool names always granted. |
| `deny` | list[str] | `[]` | Tool names always denied. |
| `ask` | list[str] | `[]` | Tool names that always prompt user. |
| `rules` | list[PermissionRuleConfig] | `[]` | Ordered rules evaluated before default mode. |

**PermissionRuleConfig:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `pattern` | str | *(required)* | Permission name or glob pattern. |
| `action` | str | `"ask"` | `allow`, `deny`, or `ask`. |

## `memory`

Episodic memory backend.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | str | `"sqlite"` | `sqlite`, `postgres`, `buri`, or custom class path. |
| `path` | str | `"~/.ravn/memory.db"` | SQLite database path. |
| `dsn` | str | `""` | PostgreSQL DSN. |
| `dsn_env` | str | `""` | Env var name for DSN (takes precedence). |
| `prefetch_budget` | int | `2000` | Max tokens of past context per turn. |
| `prefetch_limit` | int | `5` | Max episodes in prefetch. |
| `prefetch_min_relevance` | float | `0.3` | Min relevance score (0–1). |
| `recency_half_life_days` | float | `14.0` | Half-life for recency decay. |
| `max_retries` | int | `15` | Max retries on SQLite lock. |
| `min_retry_jitter_ms` | float | `20.0` | Min jitter between retries. |
| `max_retry_jitter_ms` | float | `150.0` | Max jitter between retries. |
| `checkpoint_interval` | int | `50` | Writes between WAL checkpoints. |
| `session_search_truncate_chars` | int | `100000` | Max chars per session in search. |

## `embedding`

Semantic search via vector embeddings.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable embedding-based search. |
| `adapter` | str | `"ravn.adapters.embedding.sentence_transformer.SentenceTransformerEmbeddingAdapter"` | Embedding adapter class. |
| `kwargs` | dict | `{}` | Adapter constructor args. |
| `secret_kwargs_env` | dict | `{}` | Env var names for secret args. |
| `rrf_k` | int | `60` | Reciprocal Rank Fusion constant. |
| `semantic_candidate_limit` | int | `50` | Max episodes for cosine similarity. |

## `skill`

Skill extraction, storage, and discovery.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable skill system. |
| `backend` | str | `"file"` | `file` (Markdown) or `sqlite`. |
| `path` | str | `"~/.ravn/skills.db"` | SQLite path (when backend=sqlite). |
| `suggestion_threshold` | int | `3` | Min SUCCESS episodes before synthesis. |
| `cache_max_entries` | int | `128` | In-process LRU cache size. |
| `skill_dirs` | list[str] | `[]` | Extra directories for user skills. |
| `include_builtin` | bool | `true` | Include built-in skills. |

## `iteration_budget`

Session-wide iteration limits.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `total` | int | `90` | Total iterations allowed across session. |
| `near_limit_threshold` | float | `0.8` | Fraction before "near limit" warnings. |

## `context_management`

Context window compression and prompt caching.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `compression_threshold` | float | `0.8` | Fires when 80% of context window used. |
| `protect_first_messages` | int | `2` | Messages at start preserved from compression. |
| `protect_last_messages` | int | `6` | Messages at end preserved from compression. |
| `compact_recent_turns` | int | `3` | Recent turns preserved verbatim. |
| `compression_max_tokens` | int | `1024` | Max tokens for compaction summary. |
| `prompt_cache_max_entries` | int | `16` | In-process LRU prompt cache entries. |
| `prompt_cache_dir` | str | `"~/.ravn/prompt_cache"` | Persistent prompt cache directory. |

## `mcp_servers`

Model Context Protocol server definitions. See [MCP Integration](../platform/mcp.md).

```yaml
mcp_servers:
  - name: "evals"
    enabled: true
    transport: "stdio"          # stdio | http | sse
    command: "node"
    args: ["server.js"]
    env:
      NODE_ENV: "production"
    timeout: 30.0
    connect_timeout: 10.0
    auth:
      auth_type: "api_key"      # api_key | device_flow | client_credentials
      api_key_env: "EVALS_KEY"
```

## `mcp_token_store`

MCP authentication token persistence.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | str | `"local"` | `local` (encrypted file) or `openbao`. |
| `local_path` | str | `"~/.ravn/mcp_tokens.json"` | Token file path. |

## `hooks`

Pre-tool and post-tool hook lifecycle.

```yaml
hooks:
  pre_tool:
    - adapter: "mypackage.hooks.AuditHook"
      events: ["pre_tool"]
  post_tool:
    - adapter: "mypackage.hooks.MetricsHook"
      events: ["post_tool"]
```

## `checkpoint`

Crash recovery and named snapshots. See [Checkpointing](../operations/checkpointing.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable checkpointing. |
| `backend` | str | `"local"` | `local` (disk) or `postgres`. |
| `dir` | Path | `~/.ravn/checkpoints` | Checkpoint directory. |
| `checkpoint_every_n_tools` | int | `10` | Auto-save interval (tool calls). |
| `max_checkpoints_per_task` | int | `20` | Max snapshots per task. |
| `auto_before_destructive` | bool | `true` | Save before destructive operations. |
| `budget_milestone_fractions` | list[float] | `[0.5, 0.75, 0.9]` | Save at budget percentages. |

## `sleipnir`

RabbitMQ event backbone. See [Sleipnir](../operations/sleipnir.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable Sleipnir event publishing. |
| `amqp_url_env` | str | `"SLEIPNIR_AMQP_URL"` | Env var for AMQP connection URL. |
| `exchange` | str | `"ravn.events"` | RabbitMQ topic exchange name. |
| `agent_id` | str | `""` | Agent identifier (auto: hostname). |
| `reconnect_delay_s` | float | `5.0` | Delay between reconnection attempts. |
| `publish_timeout_s` | float | `2.0` | Per-publish timeout. |

## `initiative`

Drive loop / initiative engine. See [Drive Loop](../advanced/drive-loop.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable autonomous drive loop. |
| `max_concurrent_tasks` | int | `3` | Parallel task limit. |
| `task_queue_max` | int | `50` | Queue capacity. |
| `queue_journal_path` | str | `"~/.ravn/daemon/queue.json"` | Queue persistence file. |
| `default_output_mode` | str | `"silent"` | `silent`, `ambient`, or `surface`. |
| `default_persona` | str | `""` | Default persona for tasks. |
| `heartbeat_interval_seconds` | int | `60` | Heartbeat interval. |
| `cron_tick_seconds` | float | `30.0` | Cron scheduler tick interval. |
| `trigger_adapters` | list[TriggerAdapterConfig] | `[]` | Custom trigger adapters. |

## `cascade`

Cascade coordinator for distributed task execution. See [Cascade](../advanced/cascade.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable cascade. |
| `spawn_timeout_s` | float | `30.0` | Sub-agent spawn timeout. |
| `collect_timeout_s` | float | `300.0` | Result collection timeout. |
| `collect_poll_interval_s` | float | `2.0` | Poll interval for results. |
| `mesh_delegation_timeout_s` | float | `30.0` | Mesh delegation RPC timeout. |
| `stuck_timeout_seconds` | int | `60` | Stuck agent detection threshold. |
| `loop_detection_threshold` | int | `3` | Identical consecutive calls to trigger detection. |
| `on_stuck` | str | `"replan"` | Strategy: `retry`, `replan`, `escalate`, `abort`. |
| `max_retries` | int | `2` | Max retries before escalation. |

## `mesh`

Ravn-to-Ravn mesh transport. See [Flock](../advanced/flock.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable mesh transport. |
| `adapter` | str | `"nng"` | `nng`, `sleipnir`, or `composite`. |
| `rpc_timeout_s` | float | `10.0` | RPC call timeout. |
| `own_peer_id` | str | `""` | Own peer identifier (auto: hostname). |
| `nng` | NngMeshConfig | see below | nng transport settings. |
| `sleipnir` | MeshSleipnirConfig | see below | Sleipnir transport settings. |

## `discovery`

Flock peer discovery. See [Flock](../advanced/flock.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `adapter` | str | `"mdns"` | `mdns`, `sleipnir`, `k8s`, or `composite`. |
| `realm_id_env` | str | `"RAVN_REALM_ID"` | Env var for realm identifier. |
| `heartbeat_interval_s` | float | `30.0` | Heartbeat interval. |
| `peer_ttl_s` | float | `90.0` | Seconds before peer eviction. |
| `mdns` | DiscoveryMdnsConfig | see below | mDNS settings. |
| `sleipnir` | DiscoverySleipnirConfig | see below | Sleipnir discovery settings. |
| `k8s` | DiscoveryK8sConfig | see below | Kubernetes discovery settings. |

## `gateway`

Gateway channels for external communication. See [Gateway](../operations/gateway.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Enable gateway. |
| `channels` | GatewayChannelsConfig | see below | Per-channel settings. |
| `platform` | PlatformToolsConfig | see below | Platform integration. |

Supported channels: `telegram`, `http`, `skuld`, `discord`, `slack`, `matrix`, `whatsapp`.

## `mimir`

Mímir persistent knowledge base. See [Mímir](../platform/mimir.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable Mímir. |
| `path` | str | `"~/.ravn/mimir"` | Local wiki directory. |
| `auto_distill` | bool | `true` | Auto-extract learnings from sessions. |
| `distill_min_session_minutes` | int | `5` | Min session length for distillation. |
| `idle_lint_threshold_minutes` | int | `60` | Idle time before auto-lint fires. |
| `continuation_threshold_minutes` | int | `30` | Session continuation window. |
| `categories` | list[str] | `["technical", "projects", "research", "household", "self"]` | Wiki categories. |
| `search.backend` | str | `"fts"` | `fts` (full-text) or `vector`. |
| `instances` | list[MimirInstanceConfig] | `[]` | Multi-instance configuration. |
| `write_routing` | MimirWriteRoutingConfig | `{}` | Write distribution rules. |
| `source_trigger` | MimirSourceTriggerConfig | see below | Auto-synthesis settings. |
| `staleness_trigger` | MimirStalenessTriggerConfig | see below | Refresh trigger settings. |

## `buri`

Búri typed knowledge memory substrate. See [Memory Systems](../advanced/memory.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable Búri fact graph. |
| `cluster_merge_threshold` | float | `0.15` | Cosine distance for cluster merging. |
| `extraction_model` | str | `""` | Model for fact extraction (default: reflection model). |
| `min_confidence` | float | `0.6` | Min confidence for extracted facts. |
| `session_summary_max_tokens` | int | `400` | Max tokens for rolling session summary. |
| `supersession_cosine_threshold` | float | `0.85` | Cosine threshold for fact supersession. |

## `evolution`

Self-improvement pattern extraction. See [Evolution](../operations/evolution.md).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Enable evolution system. |
| `min_new_outcomes` | int | `10` | Trigger threshold for analysis. |
| `state_path` | str | `"~/.ravn/evolution_state.json"` | Evolution state file. |
| `max_episodes_to_analyze` | int | `100` | Max episodes per pass. |
| `max_outcomes_to_analyze` | int | `50` | Max outcomes per pass. |
| `skill_suggestion_min_occurrences` | int | `3` | Min pattern occurrences for skill suggestion. |
| `error_warning_min_occurrences` | int | `3` | Min error occurrences for warning. |
| `strategy_min_occurrences` | int | `3` | Min pattern occurrences for strategy. |
| `max_skill_suggestions` | int | `5` | Max suggestions per pass. |
| `max_system_warnings` | int | `5` | Max warnings per pass. |
| `max_strategy_injections` | int | `3` | Max strategies per pass. |

## `browser`

Browser automation tool.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `backend` | str | `"local"` | `local` (Playwright) or `browserbase` (cloud). |
| `headless` | bool | `true` | Run browser headless. |
| `timeout_ms` | int | `30000` | Page load timeout. |
| `allowed_origins` | list[str] | `[]` | Allowed hostname globs. |
| `blocked_origins` | list[str] | `[]` | Blocked hostname globs. |

## `logging`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `level` | str | `"warning"` | Log level: `debug`, `info`, `warning`, `error`. |
| `format` | str | `"text"` | Log format: `text` or `json`. |

## Example Configurations

### Minimal

```yaml
anthropic:
  api_key: "sk-ant-..."
```

### Pi Mode

```yaml
llm:
  model: claude-sonnet-4-6

gateway:
  enabled: true
  channels:
    telegram:
      token_env: TELEGRAM_BOT_TOKEN
      allowed_chat_ids: [123456789]
    http:
      host: "0.0.0.0"
      port: 7477

discovery:
  adapter: mdns

mesh:
  enabled: true
  adapter: nng

permission:
  mode: full_access
```

### Infrastructure Mode

```yaml
llm:
  model: claude-sonnet-4-6
  provider:
    adapter: ravn.adapters.llm.bifrost.BifrostAdapter
    kwargs:
      base_url: "http://bifrost.ravn.svc:8080"

sleipnir:
  enabled: true
  amqp_url_env: SLEIPNIR_AMQP_URL
  exchange: ravn.events

initiative:
  enabled: true
  max_concurrent_tasks: 5

cascade:
  enabled: true

discovery:
  adapter: k8s
  k8s:
    namespace: ravn
    label_selector: "app=ravn-agent"

memory:
  backend: postgres
  dsn_env: RAVN_POSTGRES_DSN
```

### Cascade Coordinator

```yaml
cascade:
  enabled: true
  spawn_timeout_s: 30.0
  collect_timeout_s: 300.0
  stuck_timeout_seconds: 60
  on_stuck: replan

tools:
  profiles:
    coordinator:
      include_groups: [core, extended, cascade]
    worker:
      include_groups: [core]
```
