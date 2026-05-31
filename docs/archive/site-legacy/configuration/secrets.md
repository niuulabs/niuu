# Secret Injection System

This document covers the architecture, configuration, and operation of Volundr's secret injection pipeline — the system that delivers user credentials into session pods without Volundr ever handling secret values in production.

## Architecture Overview

The secret injection system has three layers:

1. **Credential Store** (`CredentialStorePort`) — persists user credentials in a pluggable backend (Infisical, Vault, file system, memory).
2. **Secret Injection Contributor** (`SecretInjectionContributor`) — orchestrates credential mapping and pod spec generation during session creation.
3. **Secret Injection Adapter** (`SecretInjectionPort`) — generates the pod spec additions (annotations, volumes, mounts) that cause secrets to appear inside the pod.

```mermaid
flowchart LR
    A[User stores credential] --> B[Credential Store<br/>Infisical / Vault / File]
    B --> C[Session created]
    C --> D[SecretInjectionContributor<br/>builds CredentialMappings]
    D --> E[SecretInjectionAdapter<br/>creates ConfigMap + Identity]
    E --> F[Pod created with<br/>agent annotations]
    F --> G[Agent init container<br/>fetches secrets]
    G --> H["/run/secrets/env.sh<br/>sourced by entrypoint"]
```

### End-to-End Flow (Production — Infisical Agent Injector)

```mermaid
sequenceDiagram
    participant User
    participant CredStore as Credential Store<br/>(Infisical)
    participant Volundr
    participant K8sAPI as Kubernetes API
    participant InfisicalSrv as Infisical Server
    participant Pod as Session Pod

    User->>CredStore: Store credential<br/>(api_key, token, etc.)
    Note over CredStore: Stored at /users/{user_id}/{cred_name}/{field}

    User->>Volundr: Create session with integrations

    Volundr->>InfisicalSrv: Create Machine Identity<br/>(session-{session_id})
    Volundr->>InfisicalSrv: Attach Kubernetes Auth<br/>(tokenReviewerJwt)
    Volundr->>InfisicalSrv: Add to project<br/>(no-access role)
    Volundr->>InfisicalSrv: Add folder-scoped privilege<br/>(/users/{user_id}/**)
    Volundr->>K8sAPI: Create ConfigMap<br/>(infisical-agent-{session_id})
    Note over K8sAPI: Contains config.yaml with<br/>Go templates + K8s auth config

    Volundr->>K8sAPI: Create Pod with annotations
    Note over Pod: org.infisical.com/inject: "true"<br/>org.infisical.com/inject-mode: "init"

    Pod->>Pod: Mutating webhook adds init container
    Pod->>InfisicalSrv: Authenticate via SA token<br/>(Kubernetes Auth)
    InfisicalSrv->>K8sAPI: TokenReview (validate SA token)
    K8sAPI-->>InfisicalSrv: Token valid
    InfisicalSrv-->>Pod: Access token (60s TTL)
    Pod->>InfisicalSrv: Fetch secrets<br/>(/users/{user_id}/{cred}/*)
    Pod->>Pod: Render Go templates<br/>→ /run/secrets/env.sh
    Pod->>Pod: Entrypoint sources env.sh
    Pod->>Pod: Delete token file

    Note over Volundr: On session cleanup:
    Volundr->>InfisicalSrv: Delete Machine Identity
    Volundr->>K8sAPI: Delete ConfigMap
```

## Adapters

### Credential Store Adapters

| Adapter | Class Path | Use Case |
|---------|-----------|----------|
| InfisicalCredentialStore | `volundr.adapters.outbound.infisical_credential_store.InfisicalCredentialStore` | Production — Infisical secret manager |
| VaultCredentialStore | `volundr.adapters.outbound.vault_credential_store.VaultCredentialStore` | Production — OpenBao/Vault KV v2 |
| FileCredentialStore | `volundr.adapters.outbound.file_credential_store.FileCredentialStore` | Local development — JSON files on disk |
| MemoryCredentialStore | `volundr.adapters.outbound.memory_credential_store.MemoryCredentialStore` | Testing — in-memory, lost on restart |

### Secret Injection Adapters

| Adapter | Class Path | Use Case |
|---------|-----------|----------|
| InfisicalAgentInjectionAdapter | `volundr.adapters.outbound.infisical_secret_injection.InfisicalAgentInjectionAdapter` | Production — Infisical Agent Injector webhook |
| FileSecretInjectionAdapter | `volundr.adapters.outbound.file_secret_injection.FileSecretInjectionAdapter` | Local development — hostPath volumes |
| InMemorySecretInjectionAdapter | `volundr.adapters.outbound.memory_secret_injection.InMemorySecretInjectionAdapter` | Testing — no-op |

---

## Production Setup: Infisical Agent Injector

### Prerequisites

- **Infisical server** (self-hosted or cloud) with Universal Auth enabled
- **Infisical Agent Injector** — mutating admission webhook deployed in the cluster
- An **Infisical project** for credential storage
- A **Universal Auth** Machine Identity for Volundr management operations (create/delete per-session identities)

### Helm Values

```yaml
# Credential store — where user credentials are persisted
credentialStore:
  adapter: "volundr.adapters.outbound.infisical_credential_store.InfisicalCredentialStore"
  kwargs:
    site_url: "https://infisical.example.com"
    project_id: "<credential-project-id>"
    environment: "prod"
  secret_kwargs_env:
    client_id: INFISICAL_CLIENT_ID
    client_secret: INFISICAL_CLIENT_SECRET

# Secret injection — how credentials get into session pods
secretInjection:
  adapter: "volundr.adapters.outbound.infisical_secret_injection.InfisicalAgentInjectionAdapter"
  kwargs:
    infisical_url: "https://infisical.example.com"
    namespace: "skuld"
    org_id: "<infisical-org-id>"
    credential_project_id: "<credential-project-id>"
    environment: "prod"
    token_ttl_seconds: 60
    kubernetes_host: "https://kubernetes.default.svc.cluster.local"
    allowed_service_accounts: "default"
  secret_kwargs_env:
    client_id: INFISICAL_CLIENT_ID
    client_secret: INFISICAL_CLIENT_SECRET
    token_reviewer_jwt: TOKEN_REVIEWER_JWT
```

The `secret_kwargs_env` field maps constructor kwargs to environment variable names. Volundr resolves these at startup, keeping secrets out of the Helm values file.

### Per-Session Machine Identity Lifecycle

Each session gets its own ephemeral Machine Identity, ensuring blast-radius isolation:

**1. Create Identity**

Volundr calls `POST /api/v1/identities` to create a Machine Identity named `session-{session_id}` in the organization with `no-access` role.

**2. Attach Kubernetes Auth**

Volundr calls `POST /api/v1/auth/kubernetes-auth/identities/{identity_id}` with:

- `kubernetesHost`: The K8s API server URL (must use FQDN, e.g. `https://kubernetes.default.svc.cluster.local`)
- `allowedNamespaces`: The session pod namespace
- `allowedNames`: Allowed ServiceAccount names
- `tokenReviewerJwt`: Long-lived SA token with `system:auth-delegator` permissions
- `accessTokenTTL` / `accessTokenMaxTTL`: Set to `token_ttl_seconds` (default 60s)

**3. Add to Project**

Volundr calls `POST /api/v1/projects/{project_id}/memberships/identities/{identity_id}` with `no-access` role. This is required before adding additional privileges.

**4. Add Folder-Scoped Privilege**

Volundr calls `POST /api/v2/identity-project-additional-privilege` with permissions:

```json
{
  "permissions": [
    {
      "subject": "secrets",
      "action": ["readValue", "describeSecret"],
      "conditions": {
        "environment": "prod",
        "secretPath": {"$glob": "/users/{user_id}/**"}
      }
    },
    {
      "subject": "secret-folders",
      "action": ["read"],
      "conditions": {
        "environment": "prod",
        "secretPath": {"$glob": "/users/{user_id}/**"}
      }
    }
  ]
}
```

The identity can only read secrets under that specific user's folder.

**5. Pod Authentication**

The pod's ServiceAccount token authenticates to Infisical via Kubernetes Auth. Infisical validates the token by calling the Kubernetes TokenReview API using the `tokenReviewerJwt`. The agent receives an access token with a 60-second TTL — enough to fetch secrets during init. The token file is deleted by the entrypoint after sourcing.

**6. Cleanup**

On session termination, Volundr:
1. Reads the ConfigMap to extract the identity ID from `volundr.niuu.io/identity-id` annotation
2. Deletes the Machine Identity (immediately revokes all access)
3. Deletes the ConfigMap

The agent also self-revokes its token on shutdown via the `org.infisical.com/agent-revoke-on-shutdown: "true"` annotation.

### Token Reviewer RBAC

The Volundr Helm chart conditionally creates these resources when the Infisical Agent adapter is configured:

**`token-reviewer.yaml`** (rendered when `secretInjection.adapter` contains `InfisicalAgent`):

- **ServiceAccount** `{release}-token-reviewer` — dedicated SA for TokenReview API access
- **Secret** `{release}-token-reviewer` — auto-populated long-lived SA token (type `kubernetes.io/service-account-token`)
- **ClusterRoleBinding** `{release}-token-reviewer` — binds the SA to `system:auth-delegator` ClusterRole

The `tokenReviewerJwt` constructor parameter should be set to the token from this Secret. It allows Infisical to validate pod ServiceAccount tokens against the Kubernetes API.

### Pod Annotations

The `InfisicalAgentInjectionAdapter.pod_spec_additions()` returns these annotations:

| Annotation | Value | Purpose |
|------------|-------|---------|
| `org.infisical.com/inject` | `"true"` | Triggers the mutating webhook |
| `org.infisical.com/inject-mode` | `"init"` | Run agent as init container (not sidecar) |
| `org.infisical.com/agent-config-map` | `infisical-agent-{session_id}` | ConfigMap with agent config + Go templates |
| `org.infisical.com/agent-revoke-on-shutdown` | `"true"` | Agent revokes its own token on exit |
| `org.infisical.com/agent-set-security-context` | `"true"` | Apply security context to init container |

These annotations flow from `PodSpecAdditions` through `FluxPodManager` to the Skuld Helm chart's `podAnnotations` value.

### Infisical Server Configuration

Required server-side settings:

- **`ALLOW_INTERNAL_IP_CONNECTIONS=true`** — the agent init container connects from within the cluster (pod IPs). Without this, Infisical's SSRF protection blocks the requests.
- **`kubernetes_host`** — must be the fully qualified DNS name (`https://kubernetes.default.svc.cluster.local`), not a short name. DNS resolution from inside the Infisical server pod must reach the K8s API.

### ConfigMap Structure

The `_build_configmap_data` method generates a `config.yaml` for the Infisical agent:

```yaml
infisical:
  address: "https://infisical.example.com"
  auth:
    type: kubernetes
    config:
      identity-id: "<per-session-identity-id>"
templates:
  - destination-path: /run/secrets/env.sh
    template-content: |
      {{- with getSecretByName "<project_id>" "prod" "/users/<user_id>/<cred_name>" "api_key" }}
      export ANTHROPIC_API_KEY='{{ .Value }}'
      {{- end }}
  - destination-path: /home/volundr/.ssh/id_rsa
    template-content: |
      {{- with getSecretByName "<project_id>" "prod" "/users/<user_id>/<cred_name>" "private_key" }}{{ .Value }}{{- end }}
```

Env var mappings are combined into a single `/run/secrets/env.sh` template. File mappings each get their own template with a distinct `destination-path`.

---

## Local Development Setup: File Adapter

For local development, no Infisical or Vault infrastructure is needed.

### Credential Store

```yaml
credentialStore:
  adapter: "volundr.adapters.outbound.file_credential_store.FileCredentialStore"
  kwargs:
    base_dir: "~/.volundr/user-credentials"
    encryption_key: ""  # Fernet key; leave empty to disable encryption
```

Credentials are stored as JSON files at `{base_dir}/{owner_type}/{owner_id}/credentials.json`. An individual file per credential is also written at `{base_dir}/{owner_type}/{owner_id}/{name}.json` for direct mounting.

When `encryption_key` is set, file contents are encrypted with Fernet (symmetric authenticated encryption). Writes are atomic (temp file + `os.replace`).

### Secret Injection

```yaml
secretInjection:
  adapter: "volundr.adapters.outbound.file_secret_injection.FileSecretInjectionAdapter"
  kwargs:
    base_dir: "~/.volundr/user-credentials"
```

The adapter mounts `{base_dir}/user/{user_id}/` into the pod at `/run/secrets/user` via a hostPath volume (`DirectoryOrCreate`). The `ensure_secret_provider_class` call is a no-op — no ConfigMap or identity management is needed.

---

## SecretType to Injection Mapping

Each `SecretType` has a registered `SecretMountStrategy` that determines validation rules and default mount behavior.

| SecretType | Mount Type | Default Destination | Required Fields | Validation |
|------------|-----------|-------------------|-----------------|------------|
| `api_key` | `ENV_FILE` | `/run/secrets/api-keys.env` | At least one key-value pair | Non-empty data |
| `oauth_token` | `ENV_FILE` | `/run/secrets/oauth-token.env` | `access_token` | `access_token` present |
| `git_credential` | `FILE` | `/home/volundr/.git-credentials` | `url` | `url` present (format: `https://user:token@host`) |
| `ssh_key` | `FILE` | `/home/volundr/.ssh/id_rsa` | `private_key` | `private_key` present |
| `tls_cert` | `TEMPLATE` | `/run/secrets/tls/` | `certificate`, `private_key` | Both `certificate` and `private_key` present |
| `generic` | `ENV_FILE` | `/run/secrets/generic.env` | At least one key-value pair | Non-empty data |

Mount types:
- **`ENV_FILE`** — rendered as `export KEY='value'` lines in a shell-sourceable file
- **`FILE`** — written as raw file content to the destination path
- **`TEMPLATE`** — rendered via Go template (used for multi-field secrets like TLS certs)

The `SecretMountStrategyRegistry` falls back to `GenericMountStrategy` for unknown types.

---

## Integration Definitions

Integration definitions (`IntegrationDefinition`) control how credentials map to session environment variables and files. They are config-driven — adding a new integration requires only a YAML change, no code modifications.

### `env_from_credentials`

Maps credential field names to environment variable names. Used for non-MCP integrations.

```yaml
# Anthropic definition
env_from_credentials:
  ANTHROPIC_API_KEY: "api_key"
```

When the session starts, the `api_key` field from the stored credential is rendered as `export ANTHROPIC_API_KEY='<value>'` in `/run/secrets/env.sh`.

### `mcp_server.env_from_credentials`

Same mechanism, but scoped to MCP server integrations:

```yaml
# GitHub definition
mcp_server:
  name: github
  command: npx
  args: ["-y", "@modelcontextprotocol/server-github"]
  env_from_credentials:
    GITHUB_PERSONAL_ACCESS_TOKEN: "token"
```

The `token` field from the credential is rendered as `export GITHUB_PERSONAL_ACCESS_TOKEN='<value>'`.

### `file_mounts`

Maps target file paths to credential field names:

```yaml
file_mounts:
  /home/volundr/.ssh/id_rsa: "private_key"
  /home/volundr/.ssh/id_rsa.pub: "public_key"
```

Each entry produces a separate Go template that writes the credential field value to the specified path.

### Adding a New Integration

1. Add an `IntegrationDefinitionConfig` entry to the `integrations.definitions` list in your Volundr config YAML (or modify `_default_integration_definitions()` in `config.py` for built-in additions).
2. Specify `env_from_credentials` and/or `file_mounts` to map credential fields.
3. Optionally add an `mcp_server` spec if the integration provides an MCP server.
4. No adapter code changes are needed unless the integration requires a custom provider (e.g. for repo browsing or issue tracking APIs).

---

## Helm Chart Resources

### `token-reviewer.yaml`

Conditionally rendered when `secretInjection.adapter` contains `InfisicalAgent`. Creates:

```
ServiceAccount:       {release}-token-reviewer
Secret:               {release}-token-reviewer  (kubernetes.io/service-account-token)
ClusterRoleBinding:   {release}-token-reviewer  → system:auth-delegator
```

The `system:auth-delegator` ClusterRole grants permission to call the TokenReview API, which Infisical uses to validate pod ServiceAccount tokens during Kubernetes Auth.

### Pod Annotation Flow

```
SecretInjectionAdapter.pod_spec_additions()
  → PodSpecAdditions.annotations
    → FluxPodManager
      → Skuld Helm chart values.podAnnotations
        → Pod metadata.annotations
```

---

## Troubleshooting

### SSRF Protection Blocking Agent Requests

**Symptom:** Agent init container logs show connection refused or blocked requests to the Infisical server.

**Cause:** Infisical's built-in SSRF protection blocks requests from internal/private IP ranges.

**Fix:** Set `ALLOW_INTERNAL_IP_CONNECTIONS=true` on the Infisical server deployment.

### DNS Resolution Failures for `kubernetes_host`

**Symptom:** Identity creation fails with "failed to attach Kubernetes Auth" — Infisical cannot reach the K8s API for TokenReview.

**Cause:** Short DNS names (e.g. `kubernetes.default.svc`) may not resolve from the Infisical server pod if it runs in a different namespace or with restricted DNS.

**Fix:** Use the fully qualified domain name: `https://kubernetes.default.svc.cluster.local`.

### TokenReview RBAC Errors

**Symptom:** Pod authentication fails. Infisical logs show `Forbidden` or `cannot create resource "tokenreviews"`.

**Cause:** The `tokenReviewerJwt` ServiceAccount does not have `system:auth-delegator` permissions.

**Fix:** Verify the ClusterRoleBinding exists:

```bash
kubectl get clusterrolebinding | grep token-reviewer
kubectl describe clusterrolebinding <release>-token-reviewer
```

Ensure the referenced ServiceAccount and Secret exist in the correct namespace:

```bash
kubectl get sa -n <namespace> | grep token-reviewer
kubectl get secret -n <namespace> | grep token-reviewer
```

### `listSecrets` Not Defined / Permission Errors

**Symptom:** Agent init container fails with permission-related errors when fetching secrets.

**Cause:** The folder-scoped privilege may not have been created, or the identity was not added to the project first.

**Fix:** Check the Volundr logs for errors during `_create_session_identity`. The sequence must be: create identity, attach K8s auth, add to project, then add privilege. If any step fails, the identity is deleted (cleanup) and the error is logged.

### Checking Agent Init Container Logs

```bash
# Find the session pod
kubectl get pods -n skuld -l volundr.niuu.io/session-id=<session-id>

# Check init container logs (the agent injector container is typically named "infisical-agent")
kubectl logs <pod-name> -n skuld -c infisical-agent-init
```

### Verifying Secrets Made It Into the Pod

```bash
# Check if env.sh was rendered
kubectl exec <pod-name> -n skuld -- cat /run/secrets/env.sh

# Check if file-mounted secrets exist
kubectl exec <pod-name> -n skuld -- ls -la /home/volundr/.ssh/
kubectl exec <pod-name> -n skuld -- ls -la /run/secrets/

# Check environment variables in the running process
kubectl exec <pod-name> -n skuld -- env | grep -E 'API_KEY|TOKEN'
```

### ConfigMap Not Created

**Symptom:** Pod starts without agent injection. No `infisical-agent-{session_id}` ConfigMap exists.

**Cause:** The `SecretInjectionContributor.contribute()` method logged a warning and returned an empty contribution.

**Fix:** Check Volundr logs for `"Failed to ensure injection config for user"` messages. Common causes:
- Infisical Universal Auth credentials are invalid or expired
- The organization ID or project ID is wrong
- Network connectivity to the Infisical server is broken
