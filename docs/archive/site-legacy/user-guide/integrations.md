# Integrations

Integrations connect external services to Volundr sessions. The operator defines available integrations in config. Users attach them to sessions during launch.

## How it works

1. **Operator** defines integration definitions in Helm values or `config.yaml`.
2. **Users** store their credentials for each integration (e.g., a Linear API key).
3. When launching a session, users select which integrations to enable.
4. The `IntegrationContributor` resolves each integration into:
    - MCP servers injected into the session pod
    - Environment variables sourced from credentials

## Built-in integration types

| Type | Examples |
|------|----------|
| `source_control` | GitHub, GitLab |
| `issue_tracker` | Linear, Jira |
| `mcp_server` | Any MCP-compatible tool |

## Defining an integration

Integration definitions live in config. Here's a complete example:

```yaml
integrations:
  definitions:
    - slug: linear
      name: Linear
      description: "Linear issue tracker"
      integration_type: issue_tracker
      adapter: "volundr.adapters.outbound.integrations.linear.LinearAdapter"
      icon: linear
      credential_schema:
        api_key:
          type: string
          required: true
          description: "Linear API key"
      mcp_server:
        name: linear
        command: mcp-server-linear
        args: []
        env_from_credentials:
          LINEAR_API_KEY: api_key
```

When a user selects this integration for a session, Volundr:

1. Fetches the user's stored `api_key` credential.
2. Starts `mcp-server-linear` in the pod with `LINEAR_API_KEY` set to the credential value.

## Standalone MCP servers

MCP servers can also be configured independently of integrations via the `mcp_servers` config section. These show up in the launch wizard for users to select, but don't require a full integration definition.

Use standalone MCP servers for tools that don't need credentials or have simple setup requirements.
