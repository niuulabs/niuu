# Getting Started

## Installation

=== "pip"

    ```bash
    pip install ravn
    ```

=== "uv"

    ```bash
    uv pip install ravn
    ```

=== "Nuitka binary"

    Ravn can be compiled into a standalone binary via `make ravn-binary`.
    No Python installation required on the target machine.

### Optional Extras

```bash
# TUI (terminal user interface for flock management)
pip install ravn[tui]

# Browser automation
pip install ravn[browser]
```

## First Run

The simplest way to use Ravn is a one-shot prompt:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
ravn run "hello"
```

For an interactive REPL session, omit the prompt:

```bash
ravn run
```

Ravn will enter a read-eval-print loop where you can issue multiple prompts,
and the agent retains context across turns.

## Config File Locations

Ravn discovers its configuration file in this order (first found wins):

1. `$RAVN_CONFIG` — environment variable pointing to a YAML file
2. `~/.ravn/config.yaml` — user home directory
3. `./ravn.yaml` — current working directory
4. `/etc/ravn/config.yaml` — system-wide

Environment variable overrides use the format `RAVN_<SECTION>__<FIELD>`
(double underscore for nesting). For example:

```bash
export RAVN_LLM__MODEL=claude-opus-4
export RAVN_MEMORY__BACKEND=postgres
```

Precedence: **env vars > YAML file > defaults**.

## Minimal Config Example

Most defaults are sensible. A minimal `ravn.yaml` only needs an API key
(or set `ANTHROPIC_API_KEY` in the environment):

```yaml
anthropic:
  api_key: "sk-ant-..."
```

A more typical setup:

```yaml
llm:
  model: "claude-sonnet-4-6"
  max_tokens: 8192

permission:
  mode: workspace_write
  workspace_root: "/home/user/projects/myapp"

memory:
  backend: sqlite
  path: "~/.ravn/memory.db"

logging:
  level: info
```

## Project Configuration (RAVN.md)

Drop a `RAVN.md` file in your project root to set per-project overrides.
Ravn discovers it by walking from the current directory up to the filesystem
root, then falls back to `~/.ravn/default.md`.

```markdown
# RAVN Project: my-api

persona: coding-agent
allowed_tools: [file, git, terminal, web]
forbidden_tools: [cascade]
permission_mode: workspace-write
thinking_enabled: true
iteration_budget: 30
notes: >
  FastAPI service. Always run tests before committing.
```

Supported fields:

| Field | Type | Description |
|-------|------|-------------|
| `persona` | string | Built-in or custom persona name |
| `allowed_tools` | list | Tool names or groups to enable |
| `forbidden_tools` | list | Tool names or groups to disable |
| `permission_mode` | string | `read_only`, `workspace_write`, `full_access`, `prompt` |
| `primary_alias` | string | Bifrost model alias (`powerful`, `balanced`, `fast`) |
| `thinking_enabled` | bool | Enable extended thinking |
| `iteration_budget` | int | Max tool-call iterations per session |
| `notes` | string | Free-text project context injected into system prompt |

## What Next?

- [Configuration Reference](../configuration/reference.md) — full config surface
- [CLI Reference](../cli/reference.md) — all commands and flags
- [Tool Reference](../tools/reference.md) — built-in tools
- [Personas](../personas/reference.md) — persona system
