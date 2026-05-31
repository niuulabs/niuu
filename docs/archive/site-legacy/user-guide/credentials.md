# Credentials & Integrations

Volundr injects external credentials into session pods so your AI agent can authenticate with third-party services. You store credentials once; Volundr handles the rest — mapping them to environment variables or files inside every session that needs them.

## Credential Types

Volundr supports six credential types. Each has a default injection strategy (environment variable or file) and required fields.

| Type | Label | Required Fields | Default Mount | Destination |
|------|-------|-----------------|---------------|-------------|
| `api_key` | API Key | `api_key` | ENV_FILE | `/run/secrets/api-keys.env` |
| `oauth_token` | OAuth Token | `access_token` (+ optional `refresh_token`) | ENV_FILE | `/run/secrets/oauth-token.env` |
| `git_credential` | Git Credential | `url` (e.g. `https://user:token@github.com`) | FILE | `/home/volundr/.git-credentials` |
| `ssh_key` | SSH Key | `private_key` (+ optional `public_key`) | FILE | `/home/volundr/.ssh/id_rsa` |
| `tls_cert` | TLS Certificate | `certificate`, `private_key` | TEMPLATE | `/run/secrets/tls/` |
| `generic` | Generic Secret | Any key-value pairs | ENV_FILE | `/run/secrets/generic.env` |

## Storing Credentials

### Via the REST API

**Create a credential:**

```bash
curl -X POST /api/v1/credentials \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-github-token",
    "secret_type": "api_key",
    "data": {"token": "ghp_xxxxxxxxxxxx"},
    "metadata": {"environment": "production"}
  }'
```

The `name` field must be lowercase alphanumeric with hyphens and underscores only (`^[a-z0-9_-]+$`), max 100 characters.

**List your credentials (metadata only, never values):**

```bash
curl /api/v1/credentials
curl /api/v1/credentials?secret_type=api_key
```

**Get a single credential:**

```bash
curl /api/v1/credentials/my-github-token
```

**Delete a credential:**

```bash
curl -X DELETE /api/v1/credentials/my-github-token
```

### Tenant (Shared) Credentials

Admins can manage organization-wide credentials that are shared across all users:

```bash
# List tenant credentials (admin only)
curl /api/v1/credentials/tenant/list

# Create a tenant credential (admin only)
curl -X POST /api/v1/credentials/tenant \
  -d '{"name": "shared-openai", "secret_type": "api_key", "data": {"api_key": "sk-..."}}'

# Delete a tenant credential (admin only)
curl -X DELETE /api/v1/credentials/tenant/shared-openai
```

### Via the Web UI

Navigate to **Settings > Credentials** to create, view, and delete credentials through the graphical interface.

### Discovering Available Types

```bash
curl /api/v1/credentials/types
```

Returns field definitions, labels, and default mount types for each credential type.

## Connecting Integrations

Integrations connect credentials to specific services. Volundr ships with built-in definitions for common services.

### Built-in Integrations

| Slug | Service | Type | Credential Field | Env Var |
|------|---------|------|-----------------|---------|
| `github` | GitHub | source_control | `token` | `GITHUB_PERSONAL_ACCESS_TOKEN` |
| `gitlab` | GitLab | source_control | `token` | `GITLAB_PERSONAL_ACCESS_TOKEN` |
| `linear` | Linear | issue_tracker | `api_key` | `LINEAR_API_KEY` |
| `anthropic` | Anthropic (Claude) | ai_provider | `api_key` | `ANTHROPIC_API_KEY` |
| `openai` | OpenAI | ai_provider | `api_key` | `OPENAI_API_KEY` |

### Setting Up an Integration

**Step 1: Store the credential.**

```bash
curl -X POST /api/v1/credentials \
  -d '{
    "name": "my-linear-key",
    "secret_type": "api_key",
    "data": {"api_key": "lin_api_xxxxxxxxxxxx"}
  }'
```

**Step 2: Create the integration connection.**

```bash
curl -X POST /api/v1/integrations \
  -d '{
    "slug": "linear",
    "integration_type": "issue_tracker",
    "credential_name": "my-linear-key",
    "config": {},
    "enabled": true
  }'
```

The `slug` field references a built-in `IntegrationDefinition`. Volundr uses the definition to determine which credential fields map to which environment variables and MCP servers.

### MCP Server Integrations

GitHub, GitLab, and Linear include MCP server specifications. When a session starts with one of these integrations enabled, Volundr:

1. Injects the credential as an environment variable (e.g. `GITHUB_PERSONAL_ACCESS_TOKEN`)
2. Configures the MCP server (e.g. `npx -y @modelcontextprotocol/server-github`) to launch with that variable

The `env_from_credentials` mapping on the MCP server spec controls which credential field maps to which env var.

### Non-MCP Integrations (Anthropic, OpenAI)

These integrations use `env_from_credentials` on the `IntegrationDefinition` directly. No MCP server is launched — the credential is simply injected as an environment variable into the session pod.

## How Credentials Flow Into Sessions

When you create a session with integrations enabled, here is what happens:

```mermaid
sequenceDiagram
    participant User
    participant Volundr
    participant SecretInjectionContributor
    participant SecretInjectionAdapter
    participant Pod

    User->>Volundr: Create session (with integrations)
    Volundr->>SecretInjectionContributor: contribute(session, context)
    SecretInjectionContributor->>SecretInjectionContributor: Build CredentialMappings from IntegrationDefinitions
    SecretInjectionContributor->>SecretInjectionAdapter: ensure_secret_provider_class(user_id, mappings, session_id)
    SecretInjectionAdapter-->>SecretInjectionContributor: OK (ConfigMap created)
    SecretInjectionContributor->>SecretInjectionAdapter: pod_spec_additions(user_id, session_id)
    SecretInjectionAdapter-->>SecretInjectionContributor: PodSpecAdditions (annotations, volumes, mounts)
    SecretInjectionContributor-->>Volundr: SessionContribution with pod_spec
    Volundr->>Pod: Create pod with annotations/volumes
    Pod->>Pod: Init container fetches secrets, renders env.sh
    Pod->>Pod: Entrypoint sources /run/secrets/env.sh
```

### What happens in the pod

1. The pod starts with annotations that trigger the secret injection mechanism (agent init container or hostPath volume).
2. Credentials are rendered to `/run/secrets/env.sh` (for env vars) or to specific file paths (for SSH keys, git credentials, etc.).
3. The entrypoint script sources `/run/secrets/env.sh`, making all mapped environment variables available to the session process.
4. File-mounted credentials (SSH keys, git credentials, TLS certs) are written to their target paths with appropriate permissions.

### Credential Mapping Resolution

For each enabled integration connection, the `SecretInjectionContributor` resolves mappings by:

1. Looking up the `IntegrationDefinition` by slug
2. Collecting `env_from_credentials` from the definition (for non-MCP integrations)
3. Collecting `env_from_credentials` from the MCP server spec (for MCP integrations)
4. Collecting `file_mounts` from the definition
5. Building a `CredentialMapping` with the credential name, env mappings, and file mappings

Direct credential references (without an integration definition) are passed through as unmapped credentials.

## Security Model

**Volundr never sees secret values in production.** The security properties depend on the configured adapter:

- **Infisical Agent Injector (production):** Volundr creates a temporary Machine Identity scoped to the user's credential folder. The init container authenticates via the pod's ServiceAccount token and fetches secrets directly from Infisical. Volundr only knows credential paths and field names — never the actual values. The identity is deleted on session cleanup, immediately revoking all access.

- **File adapter (local dev):** Credentials are stored as JSON files on the host and mounted into pods via hostPath. Volundr reads and writes these files, so it does handle secret values in this mode.

- **Memory adapter (testing):** Credentials exist only in memory. No persistence, no security guarantees.

### What the API Returns

The credential REST API **never** returns secret values. Responses include metadata only: name, type, key names, timestamps, and optional labels. There is no endpoint to retrieve the actual secret data through the API.
