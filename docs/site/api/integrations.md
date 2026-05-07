# Integrations API

The integration system provides a catalog of available integrations and per-user connection management.

All endpoints are prefixed with `/api/v1/integrations`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/catalog` | List available integration definitions |
| `GET` | `/` | List user's connections |
| `POST` | `/` | Create a connection |
| `PUT` | `/{id}` | Update a connection |
| `DELETE` | `/{id}` | Delete a connection |
| `POST` | `/{id}/test` | Test a connection |

## How it works

1. **Catalog** — integration definitions are loaded from YAML config. Each defines a slug, adapter class path, credential schema, and optional MCP server.
2. **Connections** — users create connections by selecting a catalog entry, providing credentials, and configuring adapter-specific settings.
3. **Dynamic loading** — the adapter class is imported at runtime and instantiated with stored credentials.
4. **Testing** — the test endpoint instantiates the adapter and checks connectivity.

## Integration types

| Type | Description |
|------|-------------|
| `issue_tracker` | Jira, Linear, GitHub Issues |
| `messaging` | Slack, Teams (planned) |
| `source_control` | Additional git providers |
