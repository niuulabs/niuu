# Tool Reference

Ravn ships with 35+ built-in tools organized into groups. Tools are loaded
dynamically based on the active profile, persona, and configuration.

## Tool Groups

| Group | Always Active | Description |
|-------|--------------|-------------|
| `core` | Yes | File, git, bash, web fetch, terminal, todo, ask user |
| `extended` | Default profile | Introspection, memory search, web search |
| `skill` | If `skill.enabled` | Skill discovery and execution |
| `platform` | If `gateway.platform.enabled` | Volundr/Tyr integration |
| `mimir` | If `mimir.enabled` | Knowledge base tools |
| `cascade` | If `cascade.enabled` in daemon | Distributed task delegation |

## Core Tools

### File Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `read_file` | `file:read` | Read file contents with 1-based line numbers. Supports `offset` and `limit` for pagination. Max size: 1 MB (configurable via `tools.file.max_read_bytes`). |
| `write_file` | `file:write` | Create or overwrite files within workspace. Max size: 5 MB (configurable via `tools.file.max_write_bytes`). |
| `edit_file` | `file:write` | Modify file sections with semantic diff. Specify old text and replacement. |
| `glob_search` | `file:read` | Find files using glob patterns (e.g., `src/**/*.py`). |
| `grep_search` | `file:read` | Search file contents with regex patterns. Supports context lines. |

### Git Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `git_status` | `git:read` | Current branch, ahead/behind counts, staged/unstaged file list. |
| `git_diff` | `git:read` | Unified diff of working-tree or staged changes. Supports `--cached`. |
| `git_add` | `git:write` | Stage files for commit. Accepts file paths or `.` for all. |
| `git_commit` | `git:write` | Create a commit with message and optional extended description. |
| `git_checkout` | `git:write` | Switch branches or restore files. Supports `-b` for new branch. |
| `git_log` | `git:read` | Commit history. Supports oneline/full format, `--since`, `-n`. |
| `git_pr` | `git:write` | Create pull requests using `gh` CLI. Requires GitHub CLI installed. |

### Execution Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `bash` | `bash:execute` | Execute bash commands with multi-stage security validation. Fresh subprocess per call. Output limit: 100 KB. Timeout: 120s. |
| `terminal` | `shell:execute` | Persistent shell session (local backend). State persists across calls — `cd`, `export`, shell variables carry over. Timeout: 30s per command. |
| `terminal_docker` | `shell:execute` | Persistent shell in Docker container. Only active when `tools.terminal.backend` is `docker`. |

### Web Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `web_fetch` | `web:fetch` | Fetch a URL and return readable text content. Strips HTML, truncates to 20,000 characters. Detects prompt injection patterns. |

### Task Management

| Tool | Permission | Description |
|------|-----------|-------------|
| `todo_write` | `todo:write` | Create, update, or delete todo items with status and priority. |
| `todo_read` | `todo:read` | Read the current session todo list. |

### User Interaction

| Tool | Permission | Description |
|------|-----------|-------------|
| `ask_user` | `ask_user` | Pause the agent loop and prompt the user for clarification or input. |

## Extended Tools

| Tool | Permission | Description |
|------|-----------|-------------|
| `web_search` | `web:search` | Search the web via configurable provider. Returns title, URL, snippet. Default: 5 results. |
| `ravn_state` | `introspect:read` | Runtime introspection: iteration budget remaining, active tools, permission mode, memory status, current persona, model name. |
| `ravn_reflect` | `introspect:read` | Mid-task reflection via LLM call: what has been accomplished, what remains, suggested next actions, course corrections. |
| `ravn_memory_search` | `memory:read` | Semantic search over the agent's episodic memory. Returns past episodes ranked by relevance and recency. |
| `session_search` | `memory:read` | Search all past sessions matching a query. Two-stage: FTS keyword match, then group by session. |

## Skill Tools

Available when `skill.enabled` is `true` (default).

| Tool | Permission | Description |
|------|-----------|-------------|
| `skill_list` | `skill:read` | List available skills from project-local `.ravn/skills/`, user `~/.ravn/skills/`, and built-in skill definitions. |
| `skill_run` | `skill:read` | Load and execute a skill's instruction content. Skills are Markdown files with YAML frontmatter. |

## Platform Tools

Available when `gateway.platform.enabled` is `true`.

| Tool | Permission | Description |
|------|-----------|-------------|
| `volundr_session` | `platform:api` | Manage Volundr sessions: list, create, stop, delete. |
| `volundr_git` | `platform:api` | Git operations via Volundr platform API. |
| `tyr_saga` | `platform:api` | Decompose work into Tyr sagas (distributed task breakdowns). |
| `tracker_issue` | `platform:api` | Manage issues via Tyr tracker adapters (Linear, GitHub Issues, etc.). |

## Mímir Tools

Available when `mimir.enabled` is `true` (default).

| Tool | Permission | Description |
|------|-----------|-------------|
| `mimir_ingest` | `mimir:write` | Ingest URL or raw text as immutable source. Records source hash for staleness detection. Optionally auto-derives wiki pages. |
| `mimir_query` | `mimir:read` | Natural language question → relevant wiki pages. Used for knowledge synthesis. |
| `mimir_read` | `mimir:read` | Read full content of a specific wiki page by path. |
| `mimir_write` | `mimir:write` | Create or update a wiki page (Markdown with `# Title` header). |
| `mimir_search` | `mimir:read` | Full-text keyword search. Returns matching page paths ranked by hit count. |
| `mimir_lint` | `mimir:read` | Health check: orphan pages, contradiction markers, stale sources, concept gaps. |

## Cascade Tools

Available in daemon mode when `cascade.enabled` is `true`. Built dynamically
based on cascade mode (local parallel, mesh delegation, or ephemeral spawn).

| Tool | Permission | Description |
|------|-----------|-------------|
| `task_create` | `cascade:write` | Create a sub-task for parallel execution. |
| `task_collect` | `cascade:read` | Collect results from completed sub-tasks. |
| `task_status` | `cascade:read` | Check status of a specific sub-task. |
| `task_list` | `cascade:read` | List all active and completed sub-tasks. |
| `task_stop` | `cascade:write` | Cancel a running sub-task. |

## Cron Tools

Available in daemon mode when `initiative.enabled` is `true`.

| Tool | Permission | Description |
|------|-----------|-------------|
| `cron_create` | `cron:manage` | Schedule a recurring task on a cron expression or natural language schedule. |
| `cron_list` | `cron:manage` | List all scheduled cron jobs. |
| `cron_delete` | `cron:manage` | Remove a scheduled job by ID. |

## MCP Tools

Dynamically loaded from configured [MCP servers](../platform/mcp.md).
Each tool is prefixed with the server name (e.g., `evals:run_test`).

| Tool | Permission | Description |
|------|-----------|-------------|
| `mcp_auth` | `mcp:auth` | Authenticate with a named MCP server. Supports `api_key`, `client_credentials`, `device_flow`. |
| *(dynamic)* | *(varies)* | All tools discovered from configured MCP servers. |

## Tool Profiles

Profiles control which tool groups are available. Define custom profiles in
config:

```yaml
tools:
  profiles:
    default:
      include_groups: [core, extended, skill, platform, cascade, mimir]
      include_mcp: true
    worker:
      include_groups: [core]
      include_mcp: false
```

Built-in profiles:

| Profile | Groups | MCP |
|---------|--------|-----|
| `default` | core, extended, skill, platform, cascade, mimir | Yes |
| `worker` | core | No |

Personas can further filter tools via `allowed_tools` and `forbidden_tools`.

## Custom Tools

Register custom tools via config:

```yaml
tools:
  custom:
    - adapter: "mypackage.tools.MyTool"
      name: "my_custom_tool"
      description: "Does something custom"
```

The adapter class must implement the `ToolPort` interface. Remaining keys
are passed as `**kwargs` to the constructor.
