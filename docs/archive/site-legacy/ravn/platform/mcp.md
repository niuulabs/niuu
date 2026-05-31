# MCP Integration

Ravn supports the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP)
for connecting to external tool servers. MCP servers expose tools, prompts, and
resources that Ravn can discover and use alongside built-in tools.

## Configuring MCP Servers

Define servers in `ravn.yaml`:

```yaml
mcp_servers:
  - name: "evals"
    enabled: true
    transport: "stdio"
    command: "node"
    args: ["path/to/server.js"]
    env:
      NODE_ENV: "production"
    timeout: 30.0
    connect_timeout: 10.0
    auth:
      auth_type: "api_key"
      api_key_env: "EVALS_API_KEY"
```

### Transport Types

| Transport | Description | Config |
|-----------|-------------|--------|
| `stdio` | Spawn a local process, communicate via stdin/stdout. | `command`, `args`, `env` |
| `http` | Direct HTTP calls to a remote server. | `url` |
| `sse` | Server-sent events for streaming responses. | `url` |

### Server Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | *(required)* | Unique server identifier. Used as tool name prefix. |
| `transport` | str | `"stdio"` | Transport type. |
| `command` | str | `""` | Command to spawn (stdio only). |
| `args` | list[str] | `[]` | Command arguments (stdio only). |
| `env` | dict | `{}` | Environment variables for spawned process. |
| `url` | str | `""` | Server URL (http/sse only). |
| `timeout` | float | `30.0` | Request timeout in seconds. |
| `connect_timeout` | float | `10.0` | Connection timeout in seconds. |
| `enabled` | bool | `true` | Enable/disable without removing config. |
| `auth` | MCPAuthConfig | `{}` | Authentication configuration. |

## Authentication

MCP servers can require authentication. Ravn supports three auth patterns:

### API Key

Static API key passed as a header on every request.

```yaml
auth:
  auth_type: "api_key"
  api_key_env: "MY_SERVER_API_KEY"   # env var containing the key
  api_key_header: "Authorization"     # header name (default)
  api_key_prefix: "Bearer"            # prefix (default)
```

### Device Flow (OAuth2)

Interactive browser-based authentication. Used when the server requires
user consent (e.g., GitHub OAuth apps).

```yaml
auth:
  auth_type: "device_flow"
  token_url: "https://github.com/login/oauth/access_token"
  client_id: "Iv1.abc123"
  scope: "repo read:org"
```

The agent uses the `mcp_auth` tool to initiate the flow. The user completes
authentication in a browser, and the token is cached for future use.

### Client Credentials (OAuth2)

Machine-to-machine authentication without user interaction.

```yaml
auth:
  auth_type: "client_credentials"
  token_url: "https://auth.example.com/oauth/token"
  client_id: "ravn-agent"
  client_secret_env: "MCP_CLIENT_SECRET"
  scope: "api:read api:write"
  audience: "https://api.example.com"
```

## Token Storage

Tokens are cached to avoid re-authentication on every request.

```yaml
mcp_token_store:
  backend: "local"                    # "local" or "openbao"
  local_path: "~/.ravn/mcp_tokens.json"
```

| Backend | Description |
|---------|-------------|
| `local` | Encrypted JSON file on disk. |
| `openbao` | HashiCorp OpenBao (Vault fork) for centralized secret storage. |

Tokens are automatically refreshed when they expire (if the auth type supports
refresh tokens).

## Tool Discovery

When Ravn starts, it connects to all enabled MCP servers and discovers their
tools. Discovered tools are:

1. **Prefixed** with the server name: `evals:run_test`, `github:create_issue`
2. **Checked for collisions** with built-in tools (collision = warning, MCP tool skipped)
3. **Available in the agent** alongside built-in tools

Tool schemas (input parameters, descriptions) come from the MCP server.

## Degraded Mode

If an MCP server fails to connect or crashes, Ravn continues operating with
its built-in tools. MCP failures are logged but do not block the agent.

The `mcp_auth` tool allows the agent to re-authenticate with a failed server
mid-session:

```
Tool: mcp_auth
Input: { "server": "evals" }
```

## Example: Multiple Servers

```yaml
mcp_servers:
  - name: "evals"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@company/evals-mcp"]
    auth:
      auth_type: "api_key"
      api_key_env: "EVALS_KEY"

  - name: "github"
    transport: "sse"
    url: "https://mcp.github.com/sse"
    auth:
      auth_type: "device_flow"
      token_url: "https://github.com/login/oauth/access_token"
      client_id: "Iv1.abc123"
      scope: "repo"

  - name: "internal-api"
    transport: "http"
    url: "http://internal-mcp.corp:8080"
    auth:
      auth_type: "client_credentials"
      token_url: "https://sso.corp/oauth/token"
      client_id: "ravn"
      client_secret_env: "INTERNAL_CLIENT_SECRET"
```
