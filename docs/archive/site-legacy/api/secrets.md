# Secrets & MCP Servers API

## MCP servers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/mcp-servers` | List available MCP server configs |
| `GET` | `/mcp-servers/{name}` | Get an MCP server config |

MCP servers are configured in YAML and injected into session pods. They provide additional tool capabilities to AI coding agents.

## Kubernetes secrets

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/secrets` | List available K8s secrets (metadata only) |
| `GET` | `/secrets/{name}` | Get secret metadata |
| `POST` | `/secrets` | Create a K8s secret |

These endpoints manage Kubernetes secrets that can be mounted into session pods. Only metadata (name and key names) is exposed — actual values are not returned through the API.
