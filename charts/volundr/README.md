# Volundr Helm Chart

![Version: 0.0.0-ting.160](https://img.shields.io/badge/Version-0.0.0--ting.160-informational?style=flat-square)
![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square)
![AppVersion: 0.0.0-ting.159](https://img.shields.io/badge/AppVersion-0.0.0--ting.159-informational?style=flat-square)

Helm chart for deploying **Volundr** — a self-hosted Claude Code session manager with pluggable adapters for identity, authorization, storage, secret management, and session orchestration.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.8+
- PostgreSQL database
- ReadWriteMany-capable storage class (e.g., Longhorn, NFS, EFS)
- (Optional) Flux for session orchestration
- (Optional) Envoy Gateway + GatewayClass for session routing
- (Optional) Kyverno for multi-tenant PVC isolation

## Installation

```bash
# Add the helm repository (if using OCI registry)
helm pull oci://ghcr.io/niuulabs/charts/volundr --version 0.0.0-ting.160

# Create namespace
kubectl create namespace volundr

# Install the chart
helm install volundr ./charts/volundr -n volundr

# Or install with custom values
helm install volundr ./charts/volundr -n volundr -f my-values.yaml
```

## Quick Start

1. Create the required secrets:

```bash
# Database credentials
kubectl create secret generic volundr-db -n volundr \
  --from-literal=username=volundr \
  --from-literal=password=your-db-password

# Anthropic API credentials
kubectl create secret generic volundr-anthropic-api -n volundr \
  --from-literal=ANTHROPIC_API_KEY=your-api-key
```

2. Install the chart:

```bash
helm install volundr ./charts/volundr -n volundr \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=volundr.example.com
```

## Architecture: Dynamic Adapter Pattern

Volundr uses a **dynamic adapter pattern** for all pluggable subsystems. Each adapter section in `values.yaml` follows this structure:

```yaml
<adapterSection>:
  # Fully-qualified Python class path -- dynamically imported at startup
  adapter: "volundr.adapters.outbound.<module>.<ClassName>"
  # All kwargs forwarded to the adapter constructor as **kwargs
  kwargs:
    key1: value1
    key2: value2
```

**How it works:**

1. The `adapter` value is a fully-qualified Python class path (e.g., `volundr.adapters.outbound.flux.FluxPodManager`).
2. At startup, Volundr dynamically imports the class using `importlib`.
3. All keys under `kwargs` are passed to the class constructor as keyword arguments.
4. **No code changes** are required to swap backends -- only update the Helm values.

The following subsystems use this pattern:

| Section | Port Interface | Purpose |
|---------|---------------|---------|
| [`podManager`](#pod-manager-adapter) | `PodManager` | Session pod orchestration |
| [`identity`](#identity-adapter) | `IdentityPort` | Authentication / user resolution |
| [`authorization`](#authorization-adapter) | `AuthorizationPort` | Access control decisions |
| [`credentialStore`](#credential-store-adapter) | `CredentialStorePort` | Secret storage backend |
| [`secretInjection`](#secret-injection-adapter) | `SecretInjectionPort` | CSI volume injection into session pods |
| [`storageAdapter`](#storage-adapter) | `StoragePort` | PVC lifecycle management |
| [`gateway`](#gateway-adapter) | `GatewayPort` | Session routing configuration |

## Values

<!-- README VALUES TABLE -- generated from values.yaml -->
<!-- To regenerate: run `charts/volundr/README-generate.sh` -->

### Global Parameters

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `global.imagePullSecrets` | list | `[]` | Global image pull secrets (list of secret names) |
| `global.image.registry` | string | `""` | Global image registry override |
| `global.image.repository` | string | `""` | Global image repository override |
| `global.image.tag` | string | `""` | Global image tag override |
| `nameOverride` | string | `""` | Override the name of the chart |
| `fullnameOverride` | string | `""` | Override the full name of the chart |
| `replicaCount` | int | `1` | Number of replicas (ignored if autoscaling is enabled) |
| `revisionHistoryLimit` | int | `10` | Number of old ReplicaSets to retain |

### Image

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `image.registry` | string | `"ghcr.io"` | Container registry |
| `image.repository` | string | `"niuulabs/volundr"` | Image repository |
| `image.tag` | string | `""` | Image tag (defaults to Chart.appVersion) |
| `image.pullPolicy` | string | `"Always"` | Image pull policy |

### Service Account

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `serviceAccount.create` | bool | `true` | Create a service account |
| `serviceAccount.name` | string | `""` | Service account name (defaults to fullname) |
| `serviceAccount.annotations` | object | `{}` | Annotations for the service account |
| `serviceAccount.automount` | bool | `true` | Automount API credentials |

### RBAC

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `rbac.create` | bool | `true` | Create RBAC resources |
| `rbac.clusterWide` | bool | `false` | Enable cluster-wide RBAC (ClusterRole/ClusterRoleBinding). Required when using `K8sStorageAdapter` or `K8sGatewayAdapter` to manage resources across namespaces |
| `rbac.extraRules` | list | `[]` | Additional rules for the Role |
| `rbac.extraClusterRules` | list | `[]` | Additional rules for the ClusterRole |

### Service

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `service.type` | string | `"ClusterIP"` | Service type (`ClusterIP`, `LoadBalancer`, `NodePort`) |
| `service.port` | int | `80` | Service port |
| `service.targetPort` | int | `8080` | Target port on the container |
| `service.clusterIP` | string | `""` | Cluster IP (only for ClusterIP type) |
| `service.loadBalancerIP` | string | `""` | Load balancer IP (only for LoadBalancer type) |
| `service.loadBalancerSourceRanges` | list | `[]` | Load balancer source ranges |
| `service.externalTrafficPolicy` | string | `""` | External traffic policy |
| `service.sessionAffinity` | string | `""` | Session affinity |
| `service.sessionAffinityConfig` | object | `{}` | Session affinity config |
| `service.nodePort` | string | `""` | Node port (only for NodePort type) |
| `service.annotations` | object | `{}` | Service annotations |
| `service.extraPorts` | list | `[]` | Additional service ports |

### Ingress

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `ingress.enabled` | bool | `false` | Enable ingress |
| `ingress.className` | string | `""` | Ingress class name (`nginx`, `traefik`, `haproxy`, etc.) |
| `ingress.annotations` | object | `{}` | Ingress annotations (controller-specific). See values.yaml for examples per controller |
| `ingress.hosts` | list | See below | Ingress hosts. Default: `api.example.com` at `/api/v1` |
| `ingress.tls` | list | `[]` | Ingress TLS configuration |

### Storage (Shared PVCs)

Shared PersistentVolumeClaims mounted by the Volundr API deployment. These are separate from per-user/session PVCs managed by the Storage Adapter.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `storage.sessions.enabled` | bool | `true` | Enable sessions PVC |
| `storage.sessions.storageClass` | string | `"longhorn"` | Storage class for the sessions PVC |
| `storage.sessions.accessMode` | string | `"ReadWriteMany"` | Access mode -- must be `ReadWriteMany` for shared access |
| `storage.sessions.size` | string | `"1Gi"` | Size of the sessions PVC |
| `storage.sessions.mountPath` | string | `"/volundr/sessions"` | Mount path for sessions |
| `storage.home.enabled` | bool | `true` | Enable home PVC (persistent user config across sessions) |
| `storage.home.storageClass` | string | `"longhorn"` | Storage class for the home PVC |
| `storage.home.accessMode` | string | `"ReadWriteMany"` | Access mode -- must be `ReadWriteMany` for shared access |
| `storage.home.size` | string | `"1Gi"` | Size of the home PVC (user config is small) |
| `storage.home.mountPath` | string | `"/volundr/home"` | Mount path inside session pods |

### Database

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `database.name` | string | `"volundr"` | Database name |
| `database.minPoolSize` | int | `5` | Minimum connection pool size |
| `database.maxPoolSize` | int | `20` | Maximum connection pool size |
| `database.createSecret` | bool | `false` | Create a secret for database credentials |
| `database.existingSecret` | string | `"volundr-db"` | Use an existing secret for database credentials |
| `database.userKey` | string | `"username"` | Key in secret containing the username |
| `database.passwordKey` | string | `"password"` | Key in secret containing the password |
| `database.username` | string | `""` | Username (only if `createSecret` is `true`) |
| `database.password` | string | `""` | Password (only if `createSecret` is `true`) |
| `database.extraSecretData` | object | `{}` | Extra secret data |
| `database.secretAnnotations` | object | `{}` | Secret annotations |
| `database.external.enabled` | bool | `true` | Use external database |
| `database.external.host` | string | `"postgresql.default.svc.cluster.local"` | External database host |
| `database.external.port` | int | `5432` | External database port |

### Pod Manager Adapter

Selects how Volundr deploys session pods. Uses the [dynamic adapter pattern](#architecture-dynamic-adapter-pattern).

**Available adapters:**

| Adapter | Class | Description |
|---------|-------|-------------|
| Flux (default) | `volundr.adapters.outbound.flux.FluxPodManager` | Creates HelmRelease CRs directly; Flux reconciles them into session pods |

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `podManager.adapter` | string | `"volundr.adapters.outbound.flux.FluxPodManager"` | Fully-qualified class path for the PodManager adapter |
| `podManager.existingSecret` | string | `""` | Existing secret containing the backend API token (mounted as `POD_MANAGER_TOKEN` env var) |
| `podManager.tokenKey` | string | `"token"` | Key in secret containing the token |
| `podManager.kwargs` | object | See below | All kwargs forwarded to the adapter constructor |

**FluxPodManager kwargs (default):**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `podManager.kwargs.namespace` | string | `"default"` | Kubernetes namespace for HelmReleases |
| `podManager.kwargs.chart_name` | string | `"skuld"` | Helm chart name |
| `podManager.kwargs.chart_version` | string | `"0.1.0"` | Helm chart version |
| `podManager.kwargs.source_ref_kind` | string | `"HelmRepository"` | Source reference kind |
| `podManager.kwargs.source_ref_name` | string | `"skuld"` | Source reference name |
| `podManager.kwargs.base_domain` | string | `"skuld.example.com"` | Base domain for session endpoint URLs |
| `podManager.kwargs.chat_scheme` | string | `"wss"` | WebSocket scheme for chat endpoints |
| `podManager.kwargs.code_scheme` | string | `"https"` | HTTPS scheme for code-server endpoints |
| `podManager.kwargs.chat_path` | string | `"/session"` | Path for chat WebSocket endpoint |
| `podManager.kwargs.code_path` | string | `"/"` | Path for code-server endpoint |

### Chronicle

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `chronicle.autoCreateOnStop` | bool | `true` | Auto-create chronicle when a session stops |
| `chronicle.summaryModel` | string | `"claude-haiku-4-5-20251001"` | Model used for generating session summaries |
| `chronicle.summaryMaxTokens` | int | `2000` | Max tokens for summary generation |
| `chronicle.retentionDays` | int | `0` | Retention period in days (`0` = keep forever) |

### Launch Specs

Launch specs define preloaded catalog entries for session creation, including runtime, model, resources, repositories, setup scripts, MCP servers, and workspace layout.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `launchSpecs` | list | `standard-claude`, `standard-codex` | System-scope launch specs exposed through Volundr's launch catalog |

<details>
<summary>Launch spec example</summary>

```yaml
launchSpecs:
  - name: standard-claude
    description: "Default Claude Code session"
    workloadType: session
    sessionDefinition: skuldClaude
    model: "claude-sonnet-4-6"
    cliTool: claude
    resourceConfig:
      cpu: "1"
      memory: "2Gi"
      gpu: "0"
    repos:
      - url: "https://github.com/org/repo"
        branch: main
        path: "/workspace/repo"
    setupScripts:
      - "pip install -r requirements.txt"
    workspaceLayout:
      editor: vscode
    isDefault: true
```

</details>

### Git

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `git.validateOnCreate` | bool | `true` | Validate repositories on session creation |
| `git.github.enabled` | bool | `true` | Enable GitHub provider (creates default github.com instance) |
| `git.github.existingSecret` | string | `""` | Existing secret containing GitHub token |
| `git.github.tokenKey` | string | `"token"` | Key in secret containing the token |
| `git.github.instances` | list | `[]` | Additional GitHub instances (for GitHub Enterprise). Each entry: `name`, `baseUrl`, `existingSecret`, `tokenKey`, `orgs` |
| `git.gitlab.enabled` | bool | `false` | Enable GitLab provider (creates default gitlab.com instance) |
| `git.gitlab.existingSecret` | string | `""` | Existing secret containing GitLab token |
| `git.gitlab.tokenKey` | string | `"token"` | Key in secret containing the token |
| `git.gitlab.instances` | list | `[]` | Additional GitLab instances (for self-hosted GitLab). Each entry: `name`, `baseUrl`, `existingSecret`, `tokenKey`, `orgs` |
| `git.workflow.autoBranch` | bool | `true` | Auto-create branches for new sessions |
| `git.workflow.branchPrefix` | string | `"volundr/session"` | Branch name prefix for session branches |
| `git.workflow.protectMain` | bool | `true` | Protect main/master from direct commits |
| `git.workflow.defaultMergeMethod` | string | `"squash"` | Default merge method (`merge`, `squash`, `rebase`) |
| `git.workflow.autoMergeThreshold` | float | `0.9` | Confidence threshold for auto-merge (0.0-1.0) |
| `git.workflow.notifyMergeThreshold` | float | `0.6` | Confidence threshold for notify-then-merge (0.0-1.0) |

### Envoy Sidecar Proxy

JWT validation and header extraction via an Envoy sidecar container. When enabled, Envoy becomes the service target and proxies requests to the Volundr container.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `envoy.enabled` | bool | `false` | Enable Envoy sidecar proxy |
| `envoy.image.repository` | string | `"envoyproxy/envoy"` | Envoy image repository |
| `envoy.image.tag` | string | `"v1.32-latest"` | Envoy image tag |
| `envoy.image.pullPolicy` | string | `"IfNotPresent"` | Envoy image pull policy |
| `envoy.port` | int | `8443` | Envoy listener port (becomes the service target when enabled) |
| `envoy.adminPort` | int | `9901` | Envoy admin interface port (localhost only) |
| `envoy.connectTimeout` | string | `"0.25s"` | Upstream connect timeout |
| `envoy.upstreamTimeout` | string | `"60s"` | Upstream request timeout |
| `envoy.resources.requests.cpu` | string | `"50m"` | CPU request |
| `envoy.resources.requests.memory` | string | `"64Mi"` | Memory request |
| `envoy.resources.limits.cpu` | string | `"200m"` | CPU limit |
| `envoy.resources.limits.memory` | string | `"128Mi"` | Memory limit |
| `envoy.headerNames.userId` | string | `"x-auth-user-id"` | Header name for user ID |
| `envoy.headerNames.email` | string | `"x-auth-email"` | Header name for email |
| `envoy.headerNames.tenant` | string | `"x-auth-tenant"` | Header name for tenant |
| `envoy.headerNames.roles` | string | `"x-auth-roles"` | Header name for roles |
| `envoy.jwt.enabled` | bool | `false` | Enable JWT authentication filter |
| `envoy.jwt.issuer` | string | `""` | JWT issuer URL (e.g., Keycloak realm URL) |
| `envoy.jwt.audiences` | list | `[]` | Allowed JWT audiences |
| `envoy.jwt.jwksUri` | string | `""` | JWKS URI for key retrieval |
| `envoy.jwt.jwksTimeout` | string | `"5s"` | JWKS fetch timeout |
| `envoy.jwt.jwksCacheDurationSeconds` | int | `300` | JWKS cache duration in seconds |
| `envoy.jwt.tenantClaim` | string | `"tenant_id"` | JWT claim containing tenant ID |
| `envoy.jwt.rolesClaim` | string | `"resource_access.volundr.roles"` | JWT claim containing roles (supports nested via dot notation) |
| `envoy.jwt.keycloakHost` | string | `""` | Keycloak upstream host (for JWKS fetching) |
| `envoy.jwt.keycloakPort` | int | `8080` | Keycloak upstream port |
| `envoy.jwt.keycloakTls` | bool | `false` | Enable TLS for Keycloak upstream |
| `envoy.jwt.extraClaimHeaders` | list | `[]` | Additional JWT claim-to-header mappings. Each entry: `headerName`, `claimName` |
| `envoy.extraHttpFilters` | list | `[]` | Extra HTTP filters (inserted before the router filter) |
| `envoy.extraClusters` | list | `[]` | Extra upstream clusters |

### Identity Adapter

Controls how incoming requests are resolved to users and tenants. Uses the [dynamic adapter pattern](#architecture-dynamic-adapter-pattern).

> **Note:** `user_repository` and `storage` are injected at runtime -- do not add them to `kwargs`.

**Available adapters:**

| Adapter | Class | Description |
|---------|-------|-------------|
| AllowAll (default) | `volundr.adapters.outbound.identity.AllowAllIdentityAdapter` | Development mode -- all requests are allowed with a default user |
| Envoy Headers | `volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter` | Production -- reads user/tenant/roles from headers set by Envoy JWT filter |

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `identity.adapter` | string | `"volundr.adapters.outbound.identity.AllowAllIdentityAdapter"` | Fully-qualified class path for the IdentityPort adapter |
| `identity.kwargs` | object | `{}` | All kwargs forwarded to the adapter constructor |
| `identity.roleMapping` | object | `{"admin":"volundr:admin","developer":"volundr:developer","viewer":"volundr:viewer"}` | Role mapping from IDP claim roles to Volundr roles |

<details>
<summary>EnvoyHeaderIdentityAdapter kwargs</summary>

```yaml
identity:
  adapter: "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"
  kwargs:
    user_id_header: "x-auth-user-id"
    email_header: "x-auth-email"
    tenant_header: "x-auth-tenant"
    roles_header: "x-auth-roles"
```

</details>

### Storage Adapter

Controls how per-user home PVCs and per-session workspace PVCs are created and deleted. Uses the [dynamic adapter pattern](#architecture-dynamic-adapter-pattern).

**Available adapters:**

| Adapter | Class | Description |
|---------|-------|-------------|
| InMemory (default) | `volundr.adapters.outbound.k8s_storage.InMemoryStorageAdapter` | Generates PVC names without creating Kubernetes resources. Suitable for development |
| Kubernetes | `volundr.adapters.outbound.k8s_storage_adapter.K8sStorageAdapter` | Creates/deletes real PVCs in the target namespace. Requires cluster-wide RBAC (`rbac.clusterWide: true`) |

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `storageAdapter.adapter` | string | `"volundr.adapters.outbound.k8s_storage.InMemoryStorageAdapter"` | Fully-qualified class path for the StoragePort adapter |
| `storageAdapter.kwargs` | object | `{}` | All kwargs forwarded to the adapter constructor |

<details>
<summary>K8sStorageAdapter kwargs</summary>

```yaml
storageAdapter:
  adapter: "volundr.adapters.outbound.k8s_storage_adapter.K8sStorageAdapter"
  kwargs:
    namespace: "skuld"
    home_storage_class: "longhorn"
    workspace_storage_class: "longhorn"
```

</details>

### Authorization Adapter

Controls access control decisions. Uses the [dynamic adapter pattern](#architecture-dynamic-adapter-pattern).

**Available adapters:**

| Adapter | Class | Description |
|---------|-------|-------------|
| AllowAll (default) | `volundr.adapters.outbound.authorization.AllowAllAuthorizationAdapter` | Development mode -- all actions are permitted |
| SimpleRole | `volundr.adapters.outbound.authorization.SimpleRoleAuthorizationAdapter` | Role-based access control using the identity role mapping |
| Cerbos | `volundr.adapters.outbound.cerbos.CerbosAuthorizationAdapter` | Delegates authorization to a [Cerbos PDP](https://cerbos.dev/) via HTTP. Scalable, policy-as-code authorization |

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `authorization.adapter` | string | `"volundr.adapters.outbound.authorization.AllowAllAuthorizationAdapter"` | Fully-qualified class path for the AuthorizationPort adapter |
| `authorization.kwargs` | object | `{}` | All kwargs forwarded to the adapter constructor |

<details>
<summary>CerbosAuthorizationAdapter kwargs</summary>

```yaml
authorization:
  adapter: "volundr.adapters.outbound.cerbos.CerbosAuthorizationAdapter"
  kwargs:
    url: "http://cerbos:3592"
    timeout: 5
```

</details>

### Credential Store Adapter

Controls how integration credentials (API keys, tokens) are stored and retrieved. Uses the [dynamic adapter pattern](#architecture-dynamic-adapter-pattern).

**Available adapters:**

| Adapter | Class | Description |
|---------|-------|-------------|
| Memory (default) | `volundr.adapters.outbound.memory_credential_store.MemoryCredentialStore` | In-memory store, credentials lost on restart. Suitable for development |
| OpenBao | `niuu.adapters.openbao_credential_store.OpenBaoCredentialStore` | OpenBao/Vault-compatible KV v2 backend |
| Infisical | `volundr.adapters.outbound.infisical_credential_store.InfisicalCredentialStore` | Infisical secrets management backend |

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `credentialStore.adapter` | string | `"volundr.adapters.outbound.memory_credential_store.MemoryCredentialStore"` | Fully-qualified class path for the CredentialStorePort adapter |
| `credentialStore.kwargs` | object | `{}` | All kwargs forwarded to the adapter constructor |

<details>
<summary>OpenBaoCredentialStore kwargs</summary>

```yaml
credentialStore:
  adapter: "niuu.adapters.openbao_credential_store.OpenBaoCredentialStore"
  kwargs:
    url: "http://openbao:8200"
    mount_path: "volundr"
    auth_method: "token"
    token: ""
```

</details>

<details>
<summary>InfisicalCredentialStore kwargs</summary>

```yaml
credentialStore:
  adapter: "volundr.adapters.outbound.infisical_credential_store.InfisicalCredentialStore"
  kwargs:
    site_url: "https://app.infisical.com"
    client_id: ""
    client_secret: ""
    project_id: ""
```

</details>

### Secret Injection Adapter

Controls how secrets (e.g., CSI volumes) are injected into session pods. Uses the [dynamic adapter pattern](#architecture-dynamic-adapter-pattern).

**Available adapters:**

| Adapter | Class | Description |
|---------|-------|-------------|
| InMemory (default) | `volundr.adapters.outbound.memory_secret_injection.InMemorySecretInjectionAdapter` | No-op adapter for development |
| Infisical Agent Injector | `volundr.adapters.outbound.infisical_secret_injection.InfisicalAgentInjectionAdapter` | Creates per-session Infisical identities and agent ConfigMaps |
| OpenBao Agent Injector | `volundr.adapters.outbound.openbao_secret_injection.OpenBaoAgentInjectionAdapter` | Creates session-scoped OpenBao JWT roles, ServiceAccounts, and agent ConfigMaps |

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `secretInjection.adapter` | string | `"volundr.adapters.outbound.memory_secret_injection.InMemorySecretInjectionAdapter"` | Fully-qualified class path for the SecretInjectionPort adapter |
| `secretInjection.kwargs` | object | `{}` | All kwargs forwarded to the adapter constructor |

<details>
<summary>InfisicalAgentInjectionAdapter kwargs</summary>

```yaml
secretInjection:
  adapter: "volundr.adapters.outbound.infisical_secret_injection.InfisicalAgentInjectionAdapter"
  kwargs:
    infisical_url: "https://infisical.example.com"
```

</details>

<details>
<summary>OpenBaoAgentInjectionAdapter kwargs</summary>

```yaml
secretInjection:
  adapter: "volundr.adapters.outbound.openbao_secret_injection.OpenBaoAgentInjectionAdapter"
  kwargs:
    openbao_url: "https://openbao.ymir.niuu.world"
    namespace: "skuld"
    mount_path: "volundr"
    auth_path: "jwt-example"
    audience: "https://kubernetes.default.svc.cluster.local"
```

</details>

### Gateway Adapter

Controls how session `HTTPRoute` and `SecurityPolicy` resources are configured in the Kubernetes Gateway API. Uses the [dynamic adapter pattern](#architecture-dynamic-adapter-pattern).

**Available adapters:**

| Adapter | Class | Description |
|---------|-------|-------------|
| InMemory (default) | `volundr.adapters.outbound.k8s_gateway.InMemoryGatewayAdapter` | Returns static config without touching Kubernetes. Suitable for development |
| K8s Gateway API | `volundr.adapters.outbound.k8s_gateway.K8sGatewayAdapter` | Creates/manages HTTPRoute and SecurityPolicy CRs for session routing |

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `gateway.adapter` | string | `"volundr.adapters.outbound.k8s_gateway.InMemoryGatewayAdapter"` | Fully-qualified class path for the GatewayPort adapter |
| `gateway.kwargs` | object | `{}` | All kwargs forwarded to the adapter constructor |

<details>
<summary>K8sGatewayAdapter kwargs</summary>

```yaml
gateway:
  adapter: "volundr.adapters.outbound.k8s_gateway.K8sGatewayAdapter"
  kwargs:
    namespace: "volundr"
    gateway_name: "volundr-gateway"
    gateway_namespace: "volundr"
    gateway_domain: "sessions.example.com"
    issuer_url: "https://idp.example.com"
    audience: "volundr"
    jwks_uri: "https://idp.example.com/.well-known/jwks"
    cors_origins:
      - "https://volundr.example.com"
```

</details>

### Session Gateway (Kubernetes Gateway API Resource)

The shared `Gateway` resource that all session `HTTPRoute`s attach to. Requires Envoy Gateway controller and a `GatewayClass` deployed in the cluster.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `sessionGateway.enabled` | bool | `false` | Enable the shared Gateway resource |
| `sessionGateway.name` | string | `"volundr-gateway"` | Gateway resource name |
| `sessionGateway.gatewayClassName` | string | `"eg"` | GatewayClass to use (Envoy Gateway default: `"eg"`) |
| `sessionGateway.hostname` | string | `""` | Hostname for the HTTPS listener (wildcard for session routing). external-dns creates a DNS record automatically |
| `sessionGateway.certIssuer` | string | `"letsencrypt-prod"` | cert-manager ClusterIssuer name for TLS certificate provisioning |
| `sessionGateway.tlsSecretName` | string | `""` | TLS secret name (defaults to `"{name}-tls"` if not set) |
| `sessionGateway.allowedRouteNamespaces` | string | `"All"` | Which namespaces can attach HTTPRoutes (`"All"`, `"Same"`, `"Selector"`) |
| `sessionGateway.httpRedirect` | bool | `true` | Enable HTTP listener with 301 redirect to HTTPS |
| `sessionGateway.annotations` | object | `{}` | Extra annotations on the Gateway resource |

### Application Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `config.logLevel` | string | `"info"` | Log level (`debug`, `info`, `warning`, `error`) |
| `config.logFormat` | string | `"json"` | Log format (`json`, `text`) |
| `config.host` | string | `"0.0.0.0"` | Host to bind to |
| `config.workers` | int | `4` | Number of uvicorn workers |
| `config.sessionTimeout` | string | `"3600"` | Session timeout in seconds |
| `config.maxSessionsPerUser` | string | `"5"` | Maximum sessions per user |
| `config.corsOrigins` | string | `"*"` | CORS allowed origins |
| `config.corsAllowCredentials` | string | `"true"` | CORS allow credentials |
| `config.extra` | object | `{}` | Extra configuration values |

### Existing Secrets

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `existingSecrets.anthropic` | string | `"volundr-anthropic-api"` | Name of the secret containing Anthropic API credentials. Expected keys: `ANTHROPIC_API_KEY`, optionally `ANTHROPIC_BASE_URL` |

### Session Definitions

Session definitions are Kubernetes custom resources that describe how session pods are built. They reference a Helm chart (Skuld) and provide default values that can be overridden per-session.

#### skuld-claude

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `sessionDefinitions.skuldClaude.enabled` | bool | `true` | Enable skuld-claude session definition |
| `sessionDefinitions.skuldClaude.labels` | list | `["session"]` | Labels for agent routing -- only agents with matching labels will load this definition |
| `sessionDefinitions.skuldClaude.active` | bool | `true` | Whether this session definition is active |
| `sessionDefinitions.skuldClaude.helm.chart` | string | `"skuld"` | Chart name |
| `sessionDefinitions.skuldClaude.helm.repo` | string | `"oci://ghcr.io/niuulabs/charts"` | Helm repository URL (supports `oci://` for OCI registries) |
| `sessionDefinitions.skuldClaude.helm.repoName` | string | `""` | Name of HelmRepository or OCIRepository CR in cluster (for Flux) |
| `sessionDefinitions.skuldClaude.helm.version` | string | `"0.1.0"` | Chart version constraint |
| `sessionDefinitions.skuldClaude.defaults.session.model` | string | `"claude-sonnet-4-20250514"` | Default Claude model |
| `sessionDefinitions.skuldClaude.defaults.broker.cliType` | string | `"claude"` | AI CLI backend |
| `sessionDefinitions.skuldClaude.defaults.broker.transport` | string | `"sdk"` | CLI transport mode (`sdk` = WebSocket, `subprocess` = legacy) |
| `sessionDefinitions.skuldClaude.defaults.broker.skipPermissions` | bool | `true` | Skip tool permission prompts (`--dangerously-skip-permissions`) |
| `sessionDefinitions.skuldClaude.defaults.broker.agentTeams` | bool | `false` | Enable Claude Code experimental Agent Teams |
| `sessionDefinitions.skuldClaude.defaults.image.repository` | string | `"ghcr.io/niuulabs/skuld"` | Session image repository |
| `sessionDefinitions.skuldClaude.defaults.image.tag` | string | `"latest"` | Session image tag |
| `sessionDefinitions.skuldClaude.defaults.resources` | object | See values.yaml | Resource requests/limits for the session container |
| `sessionDefinitions.skuldClaude.defaults.codeServer.enabled` | bool | `true` | Enable code-server sidecar |
| `sessionDefinitions.skuldClaude.defaults.codeServer.image.repository` | string | `"codercom/code-server"` | code-server image repository |
| `sessionDefinitions.skuldClaude.defaults.codeServer.image.tag` | string | `"latest"` | code-server image tag |
| `sessionDefinitions.skuldClaude.defaults.codeServer.resources` | object | See values.yaml | Resource requests/limits for code-server |
| `sessionDefinitions.skuldClaude.defaults.imagePullSecrets` | list | `["ghcr-pull-secret"]` | Image pull secrets for session pods |
| `sessionDefinitions.skuldClaude.defaults.ingress.className` | string | `""` | Ingress class name for sessions |
| `sessionDefinitions.skuldClaude.defaults.ingress.annotations` | object | `{}` | Controller-specific annotations for WebSocket support |
| `sessionDefinitions.skuldClaude.defaults.ingress.tls.enabled` | bool | `false` | Enable TLS for session ingress |
| `sessionDefinitions.skuldClaude.defaults.ingress.tls.secretName` | string | `"skuld-wildcard-tls"` | TLS wildcard secret name |
| `sessionDefinitions.skuldClaude.defaults.git.credentials.secretName` | string | `"github-token"` | K8s secret name containing git credentials (mounted in session pod) |
| `sessionDefinitions.skuldClaude.defaults.localServices.terminal.debug` | bool | `false` | Enable terminal debug UI in session pods |
| `sessionDefinitions.skuldClaude.defaults.persistence.mountPath` | string | `"/volundr/sessions"` | Mount path for session data |
| `sessionDefinitions.skuldClaude.defaults.homeVolume.enabled` | bool | `true` | Enable persistent home directory in session pods |
| `sessionDefinitions.skuldClaude.defaults.homeVolume.mountPath` | string | `"/volundr/home"` | Mount path for the home PVC |
| `sessionDefinitions.skuldClaude.defaults.homeVolume.credentialFiles.secretName` | string | `"claude-credentials"` | K8s secret containing Claude credential files (symlinked into home dir) |
| `sessionDefinitions.skuldClaude.defaults.homeVolume.credentialFiles.destDir` | string | `".claude"` | Subdirectory under mountPath where credential symlinks are placed |
| `sessionDefinitions.skuldClaude.defaults.homeVolume.credentialFiles.secretMountPath` | string | `"/volundr/secrets/credential-files"` | Internal mount path for the credentials secret |
| `sessionDefinitions.skuldClaude.defaults.envSecrets` | list | See values.yaml | Secrets injected as env vars into the broker container |
| `sessionDefinitions.skuldClaude.defaults.envVars` | list | `[]` | Plain env vars injected into the broker container |
| `sessionDefinitions.skuldClaude.defaults.securityContext` | object | `{"runAsNonRoot":true,"runAsUser":1000,"fsGroup":1000}` | Security context for session pods |
| `sessionDefinitions.skuldClaude.defaults.runtimeClassName` | string | `""` | Runtime class name for session pods (e.g., `"nvidia"`, `"kata"`, `"gvisor"`) |
| `sessionDefinitions.skuldClaude.defaults.volundr.apiUrl` | string | `""` | Volundr API URL for session pods (auto-wired in template) |

#### skuld-claude-interactive

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `sessionDefinitions.skuldClaudeInteractive.enabled` | bool | `true` | Enable skuld-claude-interactive session definition |
| `sessionDefinitions.skuldClaudeInteractive.displayName` | string | `"Claude Code Interactive"` | Human-readable name shown in the UI |
| `sessionDefinitions.skuldClaudeInteractive.description` | string | `"Anthropic Claude Code through a tmux-backed interactive terminal"` | Short description for the session picker |
| `sessionDefinitions.skuldClaudeInteractive.defaultModel` | string | `"claude-sonnet-4-6"` | Default model for this definition |
| `sessionDefinitions.skuldClaudeInteractive.labels` | list | `["session","interactive"]` | Labels for agent routing |
| `sessionDefinitions.skuldClaudeInteractive.compatibleProviders` | list | `["anthropic"]` | Model providers this runtime accepts |
| `sessionDefinitions.skuldClaudeInteractive.active` | bool | `true` | Whether this session definition is active |
| `sessionDefinitions.skuldClaudeInteractive.helm.chart` | string | `"skuld"` | Chart name |
| `sessionDefinitions.skuldClaudeInteractive.helm.repo` | string | `"oci://ghcr.io/niuulabs/charts"` | Helm repository URL (supports `oci://` for OCI registries) |
| `sessionDefinitions.skuldClaudeInteractive.helm.repoName` | string | `""` | Name of HelmRepository or OCIRepository CR in cluster |
| `sessionDefinitions.skuldClaudeInteractive.helm.version` | string | `"0.1.0"` | Chart version constraint |
| `sessionDefinitions.skuldClaudeInteractive.defaults.session.model` | string | `"claude-sonnet-4-6"` | Default Claude model |
| `sessionDefinitions.skuldClaudeInteractive.defaults.broker.cliType` | string | `"claude"` | AI CLI backend |
| `sessionDefinitions.skuldClaudeInteractive.defaults.broker.transport` | string | `"tmux-interactive"` | Interactive tmux transport for a real TTY |
| `sessionDefinitions.skuldClaudeInteractive.defaults.broker.transportAdapter` | string | `"skuld.transports.tmux_interactive.TmuxInteractiveTransport"` | Fully-qualified transport adapter class path |
| `sessionDefinitions.skuldClaudeInteractive.defaults.broker.skipPermissions` | bool | `true` | Skip tool permission prompts (`--dangerously-skip-permissions`) |
| `sessionDefinitions.skuldClaudeInteractive.defaults.broker.agentTeams` | bool | `false` | Enable Claude Code experimental Agent Teams |
| `sessionDefinitions.skuldClaudeInteractive.defaults.image.repository` | string | `"ghcr.io/niuulabs/skuld"` | Session image repository |
| `sessionDefinitions.skuldClaudeInteractive.defaults.image.tag` | string | `"latest"` | Session image tag |
| `sessionDefinitions.skuldClaudeInteractive.defaults.resources` | object | See values.yaml | Resource requests/limits for the session container |
| `sessionDefinitions.skuldClaudeInteractive.defaults.imagePullSecrets` | list | `["ghcr-pull-secret"]` | Image pull secrets for session pods |
| `sessionDefinitions.skuldClaudeInteractive.defaults.ingress.className` | string | `""` | Ingress class name for sessions |
| `sessionDefinitions.skuldClaudeInteractive.defaults.ingress.annotations` | object | `{}` | Controller-specific annotations for WebSocket support |
| `sessionDefinitions.skuldClaudeInteractive.defaults.ingress.tls.enabled` | bool | `false` | Enable TLS for session ingress |
| `sessionDefinitions.skuldClaudeInteractive.defaults.ingress.tls.secretName` | string | `"skuld-wildcard-tls"` | TLS wildcard secret name |
| `sessionDefinitions.skuldClaudeInteractive.defaults.git.credentials.secretName` | string | `"github-token"` | K8s secret name containing git credentials |
| `sessionDefinitions.skuldClaudeInteractive.defaults.localServices.terminal.debug` | bool | `false` | Enable terminal debug UI in session pods |
| `sessionDefinitions.skuldClaudeInteractive.defaults.persistence.mountPath` | string | `"/volundr/sessions"` | Mount path for session data |
| `sessionDefinitions.skuldClaudeInteractive.defaults.homeVolume.enabled` | bool | `true` | Enable persistent home directory in session pods |
| `sessionDefinitions.skuldClaudeInteractive.defaults.homeVolume.mountPath` | string | `"/volundr/home"` | Mount path for the home PVC |
| `sessionDefinitions.skuldClaudeInteractive.defaults.homeVolume.credentialFiles.secretName` | string | `"claude-credentials"` | K8s secret containing Claude credential files |
| `sessionDefinitions.skuldClaudeInteractive.defaults.homeVolume.credentialFiles.destDir` | string | `".claude"` | Subdirectory under mountPath where credential symlinks are placed |
| `sessionDefinitions.skuldClaudeInteractive.defaults.homeVolume.credentialFiles.secretMountPath` | string | `"/volundr/secrets/credential-files"` | Internal mount path for the credentials secret |
| `sessionDefinitions.skuldClaudeInteractive.defaults.envSecrets` | list | See values.yaml | Secrets injected as env vars into the broker container |
| `sessionDefinitions.skuldClaudeInteractive.defaults.envVars` | list | `[]` | Plain env vars injected into the broker container |
| `sessionDefinitions.skuldClaudeInteractive.defaults.securityContext` | object | `{"runAsNonRoot":true,"runAsUser":1000,"fsGroup":1000}` | Security context for session pods |
| `sessionDefinitions.skuldClaudeInteractive.defaults.runtimeClassName` | string | `""` | Runtime class name for session pods (e.g., `"nvidia"`, `"kata"`, `"gvisor"`) |
| `sessionDefinitions.skuldClaudeInteractive.defaults.volundr.apiUrl` | string | `""` | Volundr API URL for session pods (auto-wired in template) |

#### skuld-codex

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `sessionDefinitions.skuldCodex.enabled` | bool | `false` | Enable skuld-codex session definition (disabled by default) |
| `sessionDefinitions.skuldCodex.labels` | list | `["session"]` | Labels for agent routing |
| `sessionDefinitions.skuldCodex.active` | bool | `true` | Whether this session definition is active |
| `sessionDefinitions.skuldCodex.helm.chart` | string | `"skuld"` | Chart name |
| `sessionDefinitions.skuldCodex.helm.repo` | string | `"oci://ghcr.io/niuulabs/charts"` | Helm repository URL |
| `sessionDefinitions.skuldCodex.helm.repoName` | string | `""` | Name of HelmRepository or OCIRepository CR in cluster |
| `sessionDefinitions.skuldCodex.helm.version` | string | `"0.1.0"` | Chart version constraint |
| `sessionDefinitions.skuldCodex.defaults.session.model` | string | `""` | Empty uses the subscription's Codex default model |
| `sessionDefinitions.skuldCodex.defaults.broker.cliType` | string | `"codex-ws"` | Codex app-server WebSocket backend |
| `sessionDefinitions.skuldCodex.defaults.broker.transportAdapter` | string | `"skuld.transports.codex_ws.CodexWebSocketTransport"` | Fully-qualified transport adapter class path |
| `sessionDefinitions.skuldCodex.defaults.broker.skipPermissions` | bool | `true` | Skip prompts and use `approvalPolicy=never` with `danger-full-access` |
| `sessionDefinitions.skuldCodex.defaults.image.repository` | string | `"ghcr.io/niuulabs/skuld"` | Session image repository |
| `sessionDefinitions.skuldCodex.defaults.image.tag` | string | `"latest"` | Session image tag |
| `sessionDefinitions.skuldCodex.defaults.homeVolume.credentialFiles.secretName` | string | `""` | Intentionally empty; subscription auth is supplied by the central access-token broker |
| `sessionDefinitions.skuldCodex.defaults.homeVolume.credentialFiles.destDir` | string | `".codex"` | Codex stores config under `.codex/` |

> Remaining skuld-codex fields follow the same structure as skuld-claude. See `values.yaml` for full details.

### Web UI

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `web.enabled` | bool | `false` | Enable the Volundr web UI component |
| `web.replicaCount` | int | `1` | Number of web replicas |
| `web.image.registry` | string | `"ghcr.io"` | Web UI image registry |
| `web.image.repository` | string | `"niuulabs/volundr-web"` | Web UI image repository |
| `web.image.tag` | string | `""` | Web UI image tag (defaults to Chart.appVersion) |
| `web.image.pullPolicy` | string | `"Always"` | Web UI image pull policy |
| `web.service.type` | string | `"ClusterIP"` | Web UI service type |
| `web.service.port` | int | `80` | Web UI service port |
| `web.ingress.enabled` | bool | `false` | Enable web UI ingress |
| `web.ingress.className` | string | `""` | Ingress class name |
| `web.ingress.annotations` | object | `{}` | Ingress annotations |
| `web.ingress.hosts` | list | See values.yaml | Ingress hosts (default: `volundr.example.com` at `/`) |
| `web.ingress.tls` | list | `[]` | Ingress TLS |
| `web.config.apiBaseUrl` | string | `""` | Backend API base URL (auto-wired to in-cluster service if empty) |
| `web.config.oidc.authority` | string | `""` | OIDC provider discovery URL. Leave empty to disable OIDC (dev mode) |
| `web.config.oidc.clientId` | string | `""` | OIDC client ID |
| `web.config.oidc.redirectUri` | string | `""` | Redirect URI after login |
| `web.config.oidc.postLogoutRedirectUri` | string | `""` | Redirect URI after logout |
| `web.config.oidc.scope` | string | `"openid profile email"` | OIDC scopes |
| `web.resources` | object | `{"requests":{"cpu":"10m","memory":"32Mi"},"limits":{"cpu":"100m","memory":"64Mi"}}` | Resource requests/limits for the web UI |

### Resources (API Backend)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `resources.requests.cpu` | string | `"100m"` | CPU request |
| `resources.requests.memory` | string | `"256Mi"` | Memory request |
| `resources.limits.cpu` | string | `"1000m"` | CPU limit |
| `resources.limits.memory` | string | `"1Gi"` | Memory limit |

### Probes

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `livenessProbe.enabled` | bool | `true` | Enable liveness probe |
| `livenessProbe.path` | string | `"/health"` | Path for liveness probe |
| `livenessProbe.scheme` | string | `"HTTP"` | Scheme for liveness probe |
| `livenessProbe.initialDelaySeconds` | int | `10` | Initial delay seconds |
| `livenessProbe.periodSeconds` | int | `30` | Period seconds |
| `livenessProbe.timeoutSeconds` | int | `10` | Timeout seconds |
| `livenessProbe.successThreshold` | int | `1` | Success threshold |
| `livenessProbe.failureThreshold` | int | `3` | Failure threshold |
| `readinessProbe.enabled` | bool | `true` | Enable readiness probe |
| `readinessProbe.path` | string | `"/health"` | Path for readiness probe |
| `readinessProbe.scheme` | string | `"HTTP"` | Scheme for readiness probe |
| `readinessProbe.initialDelaySeconds` | int | `5` | Initial delay seconds |
| `readinessProbe.periodSeconds` | int | `10` | Period seconds |
| `readinessProbe.timeoutSeconds` | int | `5` | Timeout seconds |
| `readinessProbe.successThreshold` | int | `1` | Success threshold |
| `readinessProbe.failureThreshold` | int | `3` | Failure threshold |
| `startupProbe.enabled` | bool | `false` | Enable startup probe |
| `startupProbe.path` | string | `"/health"` | Path for startup probe |
| `startupProbe.scheme` | string | `"HTTP"` | Scheme for startup probe |
| `startupProbe.initialDelaySeconds` | int | `5` | Initial delay seconds |
| `startupProbe.periodSeconds` | int | `5` | Period seconds |
| `startupProbe.timeoutSeconds` | int | `5` | Timeout seconds |
| `startupProbe.successThreshold` | int | `1` | Success threshold |
| `startupProbe.failureThreshold` | int | `30` | Failure threshold |

### Security Context

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `podSecurityContext.runAsNonRoot` | bool | `true` | Run as non-root |
| `podSecurityContext.runAsUser` | int | `1000` | Run as user |
| `podSecurityContext.fsGroup` | int | `1000` | FS group |
| `podSecurityContext.seccompProfile.type` | string | `"RuntimeDefault"` | Seccomp profile type |
| `securityContext.allowPrivilegeEscalation` | bool | `false` | Allow privilege escalation |
| `securityContext.readOnlyRootFilesystem` | bool | `true` | Read-only root filesystem |
| `securityContext.runAsNonRoot` | bool | `true` | Run as non-root |
| `securityContext.runAsUser` | int | `1000` | Run as user |
| `securityContext.capabilities.drop` | list | `["ALL"]` | Capabilities to drop |

### Autoscaling

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `autoscaling.enabled` | bool | `false` | Enable horizontal pod autoscaling |
| `autoscaling.minReplicas` | int | `1` | Minimum replicas |
| `autoscaling.maxReplicas` | int | `10` | Maximum replicas |
| `autoscaling.targetCPUUtilizationPercentage` | int | `80` | Target CPU utilization percentage |
| `autoscaling.targetMemoryUtilizationPercentage` | string | `""` | Target memory utilization percentage |
| `autoscaling.customMetrics` | list | `[]` | Custom metrics for autoscaling |
| `autoscaling.behavior` | object | `{}` | Scaling behavior configuration |

### Pod Disruption Budget

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `podDisruptionBudget.enabled` | bool | `false` | Enable PodDisruptionBudget |
| `podDisruptionBudget.minAvailable` | int | `1` | Minimum available pods |
| `podDisruptionBudget.maxUnavailable` | string | `""` | Maximum unavailable pods |
| `podDisruptionBudget.unhealthyPodEvictionPolicy` | string | `""` | Unhealthy pod eviction policy |

### Network Policy

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `networkPolicy.enabled` | bool | `false` | Enable NetworkPolicy |
| `networkPolicy.ingressFrom` | list | `[]` | Custom ingress from selectors |
| `networkPolicy.databasePodSelector` | object | `{}` | Database pod selector for egress |
| `networkPolicy.extraIngress` | list | `[]` | Extra ingress rules |
| `networkPolicy.extraEgress` | list | `[]` | Extra egress rules |

### Scheduling & Deployment

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `strategy.type` | string | `"Recreate"` | Deployment strategy. `Recreate` avoids PVC mount conflicts during upgrades |
| `nodeSelector` | object | `{}` | Node selector |
| `tolerations` | list | `[]` | Tolerations |
| `affinity` | object | `{}` | Affinity rules |
| `topologySpreadConstraints` | list | `[]` | Topology spread constraints |
| `podAnnotations` | object | `{}` | Pod annotations |
| `podLabels` | object | `{}` | Pod labels |
| `deploymentAnnotations` | object | `{}` | Deployment annotations |
| `priorityClassName` | string | `""` | Priority class name |
| `terminationGracePeriodSeconds` | int | `30` | Termination grace period in seconds |
| `dnsConfig` | object | `{}` | DNS configuration |
| `dnsPolicy` | string | `""` | DNS policy |

### Extra Resources

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `extraEnv` | list | `[]` | Extra environment variables |
| `envFrom` | list | `[]` | Environment variables from secrets/configmaps |
| `extraVolumeMounts` | list | `[]` | Extra volume mounts |
| `extraVolumes` | list | `[]` | Extra volumes |
| `sidecars` | list | `[]` | Sidecar containers |
| `initContainers` | list | `[]` | Init containers |
| `lifecycle` | object | `{}` | Container lifecycle hooks |

### Database Migrations

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `migrations.enabled` | bool | `true` | Enable database migrations via init container |
| `migrations.image.repository` | string | `"migrate/migrate"` | Migration image repository |
| `migrations.image.tag` | string | `"v4.17.0"` | Migration image tag |
| `migrations.image.pullPolicy` | string | `"IfNotPresent"` | Migration image pull policy |
| `migrations.resources.requests.cpu` | string | `"50m"` | CPU request |
| `migrations.resources.requests.memory` | string | `"64Mi"` | Memory request |
| `migrations.resources.limits.cpu` | string | `"200m"` | CPU limit |
| `migrations.resources.limits.memory` | string | `"128Mi"` | Memory limit |

## Secrets

This chart expects secrets to be created externally (via External Secrets or manually).

### Required Secrets

| Secret Name | Keys | Description |
|-------------|------|-------------|
| `volundr-db` | `username`, `password` | PostgreSQL database credentials |
| `volundr-anthropic-api` | `ANTHROPIC_API_KEY`, (optional) `ANTHROPIC_BASE_URL` | Anthropic API credentials |

### Optional Secrets

| Secret Name | Keys | Used By |
|-------------|------|---------|
| Pod manager token (configurable) | `token` | `podManager.existingSecret` -- API token for Flux backend |
| GitHub token (configurable) | `token` | `git.github.existingSecret` -- GitHub API access |
| GitLab token (configurable) | `token` | `git.gitlab.existingSecret` -- GitLab API access |
| `claude-credentials` | (credential files) | Session pods -- Claude credential files symlinked into `$HOME/.claude/` |

### Creating Secrets

```yaml
# Database secret
apiVersion: v1
kind: Secret
metadata:
  name: volundr-db
  namespace: volundr
type: Opaque
stringData:
  username: volundr
  password: your-secure-password
```

```yaml
# Anthropic API secret
apiVersion: v1
kind: Secret
metadata:
  name: volundr-anthropic-api
  namespace: volundr
type: Opaque
stringData:
  ANTHROPIC_API_KEY: "your-api-key"
  # Optional: for custom API endpoint
  # ANTHROPIC_BASE_URL: "https://api.anthropic.com"
```

## Example Configurations

### Development (minimal)

```yaml
replicaCount: 1

resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 512Mi

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: volundr.local
      paths:
        - path: /
          pathType: Prefix
```

### Production with Envoy JWT Auth

```yaml
replicaCount: 3

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10

podDisruptionBudget:
  enabled: true
  minAvailable: 2

# Enable Envoy sidecar for JWT validation
envoy:
  enabled: true
  jwt:
    enabled: true
    issuer: "https://keycloak.example.com/realms/volundr"
    audiences:
      - volundr
    jwksUri: "https://keycloak.example.com/realms/volundr/protocol/openid-connect/certs"
    keycloakHost: "keycloak.default.svc.cluster.local"

# Switch to Envoy header identity
identity:
  adapter: "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"
  kwargs:
    user_id_header: "x-auth-user-id"
    email_header: "x-auth-email"
    tenant_header: "x-auth-tenant"
    roles_header: "x-auth-roles"

# Enable role-based authorization
authorization:
  adapter: "volundr.adapters.outbound.authorization.SimpleRoleAuthorizationAdapter"
  kwargs: {}

# Use Kubernetes PVC adapter for per-user storage isolation
storageAdapter:
  adapter: "volundr.adapters.outbound.k8s_storage_adapter.K8sStorageAdapter"
  kwargs:
    namespace: "skuld"
    home_storage_class: "longhorn"
    workspace_storage_class: "longhorn"

rbac:
  clusterWide: true  # Required for K8sStorageAdapter

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
  hosts:
    - host: volundr.example.com
      paths:
        - path: /api/v1
          pathType: Prefix
  tls:
    - secretName: volundr-tls
      hosts:
        - volundr.example.com

networkPolicy:
  enabled: true

affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app.kubernetes.io/name: volundr
          topologyKey: kubernetes.io/hostname
```

### Production with Flux PodManager

```yaml
podManager:
  adapter: "volundr.adapters.outbound.flux.FluxPodManager"
  kwargs:
    namespace: "default"
    chart_name: "skuld"
    chart_version: "0.1.0"
    source_ref_kind: "HelmRepository"
    source_ref_name: "skuld"
    timeout: "5m"
    interval: "5m"
    base_domain: "skuld.example.com"
    chat_scheme: "wss"
    code_scheme: "https"
    session_defaults:
      session:
        model: "claude-sonnet-4-20250514"
      image:
        repository: "ghcr.io/niuulabs/skuld"
        tag: "latest"
```

### Production with Vault Credential Store

```yaml
credentialStore:
  adapter: "niuu.adapters.openbao_credential_store.OpenBaoCredentialStore"
  kwargs:
    url: "http://openbao:8200"
    mount_path: "volundr"
    auth_method: "token"
```

## Storage Layout

Sessions are stored at `/volundr/sessions/{uuid}/`:

```
/volundr/sessions/{uuid}/
├── .claude/          # Claude Code configuration
└── workspace/        # User workspace files
```

## Upgrading

### From 0.1.x to 0.2.x

Version 0.2.0 adds the full Volundr service deployment.

New features in 0.2.0:
- Full deployment with liveness/readiness probes
- Service and Ingress resources
- ServiceAccount and RBAC
- HorizontalPodAutoscaler support
- PodDisruptionBudget support
- NetworkPolicy support
- ConfigMap for application configuration

## Uninstallation

```bash
helm uninstall volundr -n volundr
```

Note: The PVC will be retained by default. Delete manually if needed:

```bash
kubectl delete pvc volundr-sessions -n volundr
```

## Troubleshooting

### Check pod status

```bash
kubectl get pods -n volundr -l app.kubernetes.io/name=volundr
```

### View logs

```bash
kubectl logs -n volundr -l app.kubernetes.io/name=volundr -f
```

### Check service endpoints

```bash
kubectl get endpoints -n volundr volundr
```

### Test health endpoint

```bash
kubectl port-forward -n volundr svc/volundr 8080:80
curl http://localhost:8080/health
```

---

<!-- This README was generated from values.yaml comments using charts/volundr/README-generate.sh -->
