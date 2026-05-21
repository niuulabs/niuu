# Configuration

Volundr loads configuration from YAML with environment variable overrides.

## Config file locations

Files are checked in order — first found wins:

1. `./config.yaml`
2. `/etc/volundr/config.yaml`

## Environment variable overrides

Use double underscores for nesting. Environment variables take precedence over YAML.

```bash
DATABASE__HOST=postgres.local
DATABASE__PORT=5432
GIT__GITHUB__TOKEN=ghp_xxxx
```

## Priority order

1. Constructor arguments (for testing)
2. Environment variables
3. YAML config file
4. `/run/secrets` files

## Essential configuration

These are the settings most local-mode users need.

### `database`

PostgreSQL connection settings.

| Key | Default | Description |
|-----|---------|-------------|
| `host` | `localhost` | Database host |
| `port` | `5432` | Database port |
| `user` | `volundr` | Database user |
| `password` | `volundr` | Database password |
| `name` | `volundr` | Database name |
| `min_pool_size` | `5` | Minimum connection pool size |
| `max_pool_size` | `20` | Maximum connection pool size |

### `logging`

| Key | Default | Description |
|-----|---------|-------------|
| `level` | `info` | Log level (debug, info, warning, error) |
| `format` | `text` | Log format (`text` or `json`) |

### `git`

Git provider configuration.

#### `git.github`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable GitHub integration |
| `token` | `null` | Default GitHub token |
| `base_url` | `https://api.github.com` | API base URL |
| `instances` | `[]` | List of GitHub instances (see below) |

Each instance in `instances`:

```yaml
git:
  github:
    instances:
      - name: "GitHub"
        base_url: "https://api.github.com"
        token: "ghp_xxxx"          # or use token_env
        token_env: "GITHUB_TOKEN"  # env var name containing the token
        orgs: ["my-org"]
```

Token resolution per instance: explicit `token` > env var from `token_env` > top-level `git.github.token`.

#### `git.gitlab`

Same structure as `git.github`, with `base_url` defaulting to `https://gitlab.com`.

#### `git.workflow`

| Key | Default | Description |
|-----|---------|-------------|
| `auto_branch` | `true` | Auto-create branches for sessions |
| `branch_prefix` | `volundr/session` | Branch name prefix |
| `protect_main` | `true` | Prevent direct pushes to main |
| `default_merge_method` | `squash` | Merge method (merge, squash, rebase) |
| `auto_merge_threshold` | `0.9` | Confidence score for auto-merge |
| `notify_merge_threshold` | `0.6` | Confidence score for notify-then-merge |

### `chronicle`

| Key | Default | Description |
|-----|---------|-------------|
| `auto_create_on_stop` | `true` | Auto-create chronicle when session stops |
| `summary_model` | `claude-haiku-4-5-20251001` | Model for generating summaries |
| `summary_max_tokens` | `2000` | Max tokens for summary generation |
| `retention_days` | `null` | Days to keep chronicles (null = forever) |

### `linear`

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable Linear integration |
| `api_key` | `null` | Linear API key |

### `provisioning`

| Key | Default | Description |
|-----|---------|-------------|
| `timeout_seconds` | `300.0` | Max time to wait for infrastructure readiness |
| `initial_delay_seconds` | `5.0` | Delay before starting readiness polls |

---

## Local mode vs Kubernetes

| Feature | Local (mini) | k3s | Production K8s |
|---------|:---:|:---:|:---:|
| Embedded PostgreSQL | Yes | No | No |
| Multi-user | No | Yes | Yes |
| OIDC authentication | No | Optional | Yes |
| Persistent volumes | Local disk | Yes | Yes |
| Resource limits per session | No | Yes | Yes |
| Secret management (Vault, Infisical) | No | Optional | Yes |
| Event pipeline (RabbitMQ, OTel) | Optional | Optional | Yes |
| Gateway routing | No | Yes | Yes |

---

## Advanced configuration (Kubernetes only)

The following settings are relevant for k3s and production Kubernetes deployments. Local-mode users can skip this section.

For full details, see the [Configuration Reference](../configuration/overview.md).

### `pod_manager`

Dynamic adapter for session pod orchestration.

| Key | Default | Description |
|-----|---------|-------------|
| `adapter` | `volundr.adapters.outbound.pod_manager.PodManager` | Fully-qualified class path |
| `kwargs` | `{}` | Extra kwargs passed to the adapter constructor |

### `identity`

Dynamic adapter for authentication.

| Key | Default | Description |
|-----|---------|-------------|
| `adapter` | `...AllowAllIdentityAdapter` | Fully-qualified class path |
| `kwargs` | `{}` | Extra kwargs for the adapter |
| `role_mapping` | see below | Maps IDP roles to Volundr roles |

Default role mapping:

```yaml
identity:
  role_mapping:
    admin: "volundr:admin"
    developer: "volundr:developer"
    viewer: "volundr:viewer"
```

For production with Envoy:

```yaml
identity:
  adapter: "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"
  kwargs:
    user_id_header: "x-auth-user-id"
    email_header: "x-auth-email"
```

### `authorization`

| Key | Default | Description |
|-----|---------|-------------|
| `adapter` | `...AllowAllAuthorizationAdapter` | Fully-qualified class path |
| `kwargs` | `{}` | Extra kwargs for the adapter |

### `credential_store`

| Key | Default | Description |
|-----|---------|-------------|
| `adapter` | `...MemoryCredentialStore` | Fully-qualified class path |
| `kwargs` | `{}` | Extra kwargs for the adapter |

Available adapters: `MemoryCredentialStore`, `VaultCredentialStore`, `InfisicalCredentialStore`.

### `secret_injection`

| Key | Default | Description |
|-----|---------|-------------|
| `adapter` | `...InMemorySecretInjectionAdapter` | Fully-qualified class path |
| `kwargs` | `{}` | Extra kwargs for the adapter |

### `storage`

| Key | Default | Description |
|-----|---------|-------------|
| `adapter` | `...InMemoryStorageAdapter` | Fully-qualified class path |
| `kwargs` | `{}` | Extra kwargs for the adapter |

### `gateway`

| Key | Default | Description |
|-----|---------|-------------|
| `adapter` | `...InMemoryGatewayAdapter` | Fully-qualified class path |
| `kwargs` | `{}` | Extra kwargs for the adapter |

### `event_pipeline`

| Key | Default | Description |
|-----|---------|-------------|
| `postgres_buffer_size` | `1` | Buffer size for PostgreSQL event sink |

### `session_contributors`

List of dynamic contributors that build session specs:

```yaml
session_contributors:
  - adapter: "volundr.adapters.outbound.contributors.CoreSessionContributor"
    kwargs:
      base_domain: "volundr.local"
  - adapter: "volundr.adapters.outbound.contributors.TemplateContributor"
```

### `profiles`

List of forge profiles (config-driven, read-only):

```yaml
profiles:
  - name: "default"
    description: "Default session profile"
    workload_type: "session"
    model: "claude-sonnet-4-20250514"
    is_default: true
    resource_config:
      cpu: "2"
      memory: "4Gi"
```

### `templates`

List of workspace templates (config-driven, read-only):

```yaml
templates:
  - name: "python-project"
    description: "Python development workspace"
    repos:
      - url: "github.com/org/repo"
        branch: "main"
    setup_scripts:
      - "uv sync --dev"
    is_default: false
```

### `mcp_servers`

Available MCP servers for session injection:

```yaml
mcp_servers:
  - name: "filesystem"
    type: "stdio"
    command: "npx"
    args: ["-y", "@anthropic/mcp-filesystem"]
    description: "File system access"
```

### `integrations`

Integration catalog for dynamic adapter loading:

```yaml
integrations:
  definitions:
    - slug: "linear"
      name: "Linear"
      integration_type: "issue_tracker"
      adapter: "volundr.adapters.outbound.linear.LinearAdapter"
      credential_schema:
        api_key: { type: "string", required: true }
```
