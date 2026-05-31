# Presets

Presets are user-created, portable runtime configurations stored in the database. They let you save and reuse your preferred session setup without reconfiguring everything each time.

Unlike profiles and templates (which are operator-managed and loaded from YAML config), presets belong to you and live in the database.

## What a preset captures

- Model selection
- MCP server configuration
- Resource allocation
- Environment variables
- CLI tool type (claude or codex)

```json
{
  "name": "my-claude-setup",
  "cli_tool": "claude",
  "model": "claude-sonnet-4-20250514",
  "mcp_servers": [
    {
      "name": "linear",
      "command": "npx",
      "args": ["-y", "@anthropic-ai/linear-mcp-server"]
    }
  ],
  "env_vars": {
    "EDITOR": "vim"
  },
  "is_default": true
}
```

## Creating presets

**API** — `POST /api/v1/forge/presets` with a JSON body.

**Web UI** — Save your current session configuration as a preset from the session settings panel.

## Using presets

When creating a session, pass `preset_id` to apply a saved configuration.

Preset values merge with template and profile defaults. Where there is a conflict, the preset value takes precedence.

## Default presets

You can mark one preset as default per CLI tool type. When you create a new session without specifying a preset, Volundr uses your default preset for the selected tool type.

Set `is_default: true` on the preset you want as your default. Setting a new default automatically clears the previous one for that tool type.

## Presets vs. profiles and templates

| | Presets | Profiles & Templates |
|---|---|---|
| **Owned by** | User | Operator |
| **Stored in** | Database | YAML config / CRDs |
| **Editable by users** | Yes | No |
| **Scope** | Per-user | Platform-wide |

Presets sit on top of the configuration stack. They override values from profiles and templates but do not replace them — unset fields fall through to the underlying profile or template defaults.
