# Personas

Personas configure Ravn's behavior, tool access, permission mode, model selection,
and iteration budget. They provide a coherent "role" that shapes the agent for
specific workflows.

## Built-in Personas

### `coding-agent`

General-purpose software engineering agent.

| Setting | Value |
|---------|-------|
| Tools | file, git, terminal, web, todo, introspection |
| Forbidden | cascade, volundr |
| Permission | `workspace-write` |
| Model alias | `balanced` |
| Thinking | Enabled |
| Budget | 40 iterations |

### `research-agent`

Information gathering and synthesis — no mutation.

| Setting | Value |
|---------|-------|
| Tools | web, file, introspection |
| Forbidden | git, terminal, cascade |
| Permission | `read-only` |
| Model alias | `balanced` |
| Thinking | Disabled |
| Budget | 30 iterations |

### `planning-agent`

Structured planning and architecture — read-only, high reasoning.

| Setting | Value |
|---------|-------|
| Tools | file, introspection |
| Forbidden | git, terminal, cascade, volundr |
| Permission | `read-only` |
| Model alias | `powerful` |
| Thinking | Enabled |
| Budget | 20 iterations |

### `autonomous-agent`

Fully unsupervised execution — all tools, all permissions.

| Setting | Value |
|---------|-------|
| Tools | All |
| Forbidden | None |
| Permission | `full-access` |
| Model alias | `powerful` |
| Thinking | Enabled |
| Budget | 100 iterations |

### `mimir-curator`

Knowledge base synthesis and maintenance.

| Setting | Value |
|---------|-------|
| Tools | mimir, web, file, introspection |
| Forbidden | git, terminal, cascade, volundr |
| Permission | `workspace-write` |
| Model alias | `balanced` |
| Thinking | Disabled |
| Budget | 60 iterations |

## Custom Personas

Create custom personas as YAML files in `~/.ravn/personas/`:

```yaml
# ~/.ravn/personas/my-agent.yaml
name: my-agent
description: "Custom agent for my workflow"

system_prompt: |
  You are a specialized agent for data pipeline work.
  Always validate schemas before writing.

allowed_tools:
  - file
  - git
  - bash
  - web_fetch

forbidden_tools:
  - cascade
  - volundr

permission_mode: workspace-write
primary_alias: balanced

thinking:
  enabled: true
  budget_tokens: 10000

iteration_budget: 50
```

### Persona Fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique identifier |
| `description` | string | Human-readable description |
| `system_prompt` | string | Override system prompt |
| `allowed_tools` | list | Tool names or groups to enable |
| `forbidden_tools` | list | Tool names or groups to disable |
| `permission_mode` | string | `read-only`, `workspace-write`, `full-access`, `prompt` |
| `primary_alias` | string | Bifrost model alias: `powerful`, `balanced`, `fast` |
| `thinking.enabled` | bool | Enable extended thinking |
| `thinking.budget_tokens` | int | Thinking token budget |
| `iteration_budget` | int | Max tool-call iterations |

## RAVN.md Project Overlay

A `RAVN.md` file in the project root merges with the active persona. Project
settings take precedence for fields that are set.

**Merge priority** (highest wins):

1. **RAVN.md** — project-level overrides
2. **Persona** — role-based defaults
3. **Config YAML** — global configuration
4. **Built-in defaults** — hardcoded fallbacks

For example, if the persona sets `iteration_budget: 40` but `RAVN.md` sets
`iteration_budget: 25`, the effective budget is 25.

## Persona Loading Priority

When `--persona` is specified on the CLI:

1. Check `~/.ravn/personas/<name>.yaml` (user-defined)
2. Check built-in personas (coding-agent, research-agent, etc.)
3. Error if not found

When loading from `RAVN.md`:

1. Parse the `persona:` field from `RAVN.md`
2. Resolve the named persona (user-defined or built-in)
3. Merge `RAVN.md` fields on top

## Persona Source Adapter

The persona loading mechanism is pluggable via configuration:

```yaml
persona_source:
  adapter: "ravn.adapters.personas.loader.PersonaLoader"
  kwargs: {}
```

Custom persona sources can load personas from a database, API, or other
backend by implementing the persona source interface.
