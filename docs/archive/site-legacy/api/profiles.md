# Profiles & Templates API

Profiles and templates are configuration-driven session blueprints. Profiles define runtime settings (model, resources, MCP servers). Templates define workspace layouts (repos, setup scripts) and include runtime settings.

All endpoints are prefixed with `/api/v1/forge`.

## Profile endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/profiles` | List profiles (optional workload_type filter) |
| `GET` | `/profiles/{name}` | Get a profile by name |
| `POST` | `/profiles` | Create a profile |
| `PUT` | `/profiles/{name}` | Update a profile |
| `DELETE` | `/profiles/{name}` | Delete a profile |

## Template endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/templates` | List templates (optional workload_type filter) |
| `GET` | `/templates/{name}` | Get a template by name |

Templates are read-only — they come from YAML config or Kubernetes CRDs.

## Profile model

```json
{
  "name": "gpu-heavy",
  "description": "GPU-accelerated session",
  "workload_type": "session",
  "model": "claude-sonnet-4-20250514",
  "system_prompt": "You are a helpful assistant...",
  "resource_config": {
    "cpu": "4",
    "memory": "16Gi",
    "gpu": "1"
  },
  "mcp_servers": [
    {"name": "filesystem", "type": "stdio"}
  ],
  "env_vars": {"PYTHONPATH": "/workspace"},
  "is_default": false
}
```

## Template model

Templates extend profiles with workspace configuration:

```json
{
  "name": "python-monorepo",
  "description": "Multi-package Python workspace",
  "repos": [
    {"url": "github.com/org/repo", "branch": "main", "path": "/workspace/repo"}
  ],
  "setup_scripts": ["uv sync --dev", "npm install"],
  "workspace_layout": {"root": "/workspace"},
  "workload_type": "session",
  "model": "claude-sonnet-4-20250514",
  "is_default": false
}
```
