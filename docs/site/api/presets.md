# Presets API

Presets are portable, database-stored runtime configurations. Unlike profiles (config-driven, read-only), presets can be created and managed by users through the API.

All endpoints are prefixed with `/api/v1/forge`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/presets` | List presets (optional cli_tool, is_default filters) |
| `GET` | `/presets/{id}` | Get a preset |
| `POST` | `/presets` | Create a preset |
| `PUT` | `/presets/{id}` | Update a preset |
| `DELETE` | `/presets/{id}` | Delete a preset |

## Preset model

```json
{
  "id": "uuid",
  "name": "fast-iteration",
  "description": "Quick coding with Haiku",
  "is_default": false,
  "cli_tool": "claude",
  "workload_type": "session",
  "model": "claude-haiku-4-5-20251001",
  "system_prompt": null,
  "resource_config": {"cpu": "2", "memory": "4Gi"},
  "mcp_servers": [],
  "terminal_sidecar": {},
  "skills": [],
  "rules": [],
  "env_vars": {},
  "env_secret_refs": [],
  "workload_config": {}
}
```

Setting `is_default: true` clears the default flag from other presets with the same `cli_tool`.
