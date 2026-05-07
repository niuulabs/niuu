# Helm Values Reference

Complete reference for the Volundr Helm chart. All values have sensible defaults. Override only what you need.

Install from OCI:

```bash
helm install volundr oci://ghcr.io/niuulabs/charts/volundr -n volundr -f values.yaml
```

---

## Image and Replicas

| Key | Default | Description |
|-----|---------|-------------|
| `replicaCount` | `1` | Number of API replicas (ignored if autoscaling enabled) |
| `image.registry` | `ghcr.io` | Container registry |
| `image.repository` | `niuulabs/volundr` | Image repository |
| `image.tag` | `""` (Chart.appVersion) | Image tag |
| `image.pullPolicy` | `Always` | Image pull policy |
| `revisionHistoryLimit` | `10` | Old ReplicaSets to retain |

---

## Service

| Key | Default | Description |
|-----|---------|-------------|
| `service.type` | `ClusterIP` | Service type |
| `service.port` | `80` | Service port |
| `service.targetPort` | `8080` | Container port |
| `service.clusterIP` | `""` | Cluster IP (ClusterIP type only) |
| `service.nodePort` | `""` | Node port (NodePort type only) |
| `service.annotations` | `{}` | Service annotations |

---

## Ingress

| Key | Default | Description |
|-----|---------|-------------|
| `ingress.enabled` | `false` | Enable ingress |
| `ingress.className` | `""` | Ingress class (nginx, traefik, haproxy) |
| `ingress.annotations` | `{}` | Controller-specific annotations |
| `ingress.hosts` | see below | Host and path rules |
| `ingress.tls` | `[]` | TLS configuration |

Default host:

```yaml
hosts:
  - host: api.example.com
    paths:
      - path: /api/v1
        pathType: Prefix
```

Annotation examples by controller:

```yaml
# NGINX
nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
nginx.ingress.kubernetes.io/proxy-body-size: "50m"

# Traefik
traefik.ingress.kubernetes.io/router.middlewares: default-timeout@kubernetescrd

# HAProxy
haproxy.org/timeout-server: "3600s"
```

---

## Database

| Key | Default | Description |
|-----|---------|-------------|
| `database.name` | `volundr` | Database name |
| `database.minPoolSize` | `5` | Minimum connection pool size |
| `database.maxPoolSize` | `20` | Maximum connection pool size |
| `database.existingSecret` | `volundr-db` | Secret with DB credentials |
| `database.userKey` | `username` | Key in secret for username |
| `database.passwordKey` | `password` | Key in secret for password |
| `database.createSecret` | `false` | Create secret (set username/password below) |
| `database.external.enabled` | `true` | Use external database |
| `database.external.host` | `postgresql.default.svc.cluster.local` | Database host |
| `database.external.port` | `5432` | Database port |

---

## Pod Manager

Controls how session pods are deployed. Uses the dynamic adapter pattern.

| Key | Default | Description |
|-----|---------|-------------|
| `podManager.adapter` | `volundr.adapters.outbound.flux.FluxPodManager` | Fully-qualified class path |
| `podManager.existingSecret` | `""` | Secret with backend API token |
| `podManager.tokenKey` | `token` | Key in secret for token |
| `podManager.kwargs` | see below | Constructor kwargs |

Default kwargs:

```yaml
kwargs:
  namespace: "default"
  chart_name: "skuld"
  chart_version: "0.1.0"
  source_ref_kind: "HelmRepository"
  source_ref_name: "skuld"
  base_domain: "skuld.valhalla.asgard.niuu.world"
  chat_scheme: "wss"
  code_scheme: "https"
  chat_path: "/session"
  code_path: "/"
```

---

## Provisioning

| Key | Default | Description |
|-----|---------|-------------|
| `provisioning.timeoutSeconds` | `300.0` | Max wait for infrastructure readiness |
| `provisioning.initialDelaySeconds` | `5.0` | Delay before starting readiness polls |

---

## Profiles

Profiles define base configurations for sessions. Array of objects.

```yaml
profiles:
  - name: standard
    description: "Standard Claude Code session"
    workloadType: session
    sessionDefinition: skuld-claude
    model: "claude-sonnet-4-20250514"
    resourceConfig:
      cpu: "500m"
      memory: "1Gi"
    mcpServers: []
    envVars: {}
    envSecretRefs: []
    workloadConfig: {}
    isDefault: true

  - name: gpu-heavy
    description: "GPU-accelerated session"
    workloadType: session
    sessionDefinition: skuld-claude
    model: "claude-sonnet-4-20250514"
    resourceConfig:
      cpu: "2"
      memory: "8Gi"
      gpu: "1"
    isDefault: false
```

| Field | Description |
|-------|-------------|
| `name` | Unique profile name |
| `description` | Human-readable description |
| `workloadType` | Workload type (`session`) |
| `sessionDefinition` | Which session definition to use (`skuld-claude`, `skuld-codex`) |
| `model` | Default AI model |
| `resourceConfig` | CPU, memory, GPU requests |
| `mcpServers` | MCP servers to inject |
| `envVars` | Plain environment variables |
| `envSecretRefs` | Secret-backed environment variables |
| `isDefault` | Use as default profile |

---

## Templates

Templates combine a profile with repositories and setup scripts.

```yaml
templates:
  - name: default-session
    description: "Default coding session"
    profileName: standard
    repos:
      - url: "https://github.com/org/repo"
        branch: main
    setupScripts:
      - "pip install -r requirements.txt"
    workspaceLayout:
      editor: vscode
    isDefault: true
```

| Field | Description |
|-------|-------------|
| `name` | Unique template name |
| `profileName` | Profile to inherit from |
| `repos` | Repositories to clone (url, branch) |
| `setupScripts` | Scripts to run after clone |
| `workspaceLayout` | Editor layout config |
| `isDefault` | Use as default template |

---

## Git

### Providers

```yaml
git:
  validateOnCreate: true
  github:
    enabled: true
    existingSecret: ""
    tokenKey: token
    instances: []
  gitlab:
    enabled: false
    existingSecret: ""
    tokenKey: token
    instances: []
```

Add GitHub Enterprise or self-hosted GitLab instances:

```yaml
github:
  instances:
    - name: GitHub Enterprise
      baseUrl: https://github.company.com/api/v3
      existingSecret: github-enterprise-secret
      tokenKey: token
      orgs:
        - engineering
```

### Workflow

| Key | Default | Description |
|-----|---------|-------------|
| `git.workflow.autoBranch` | `true` | Auto-create branches for sessions |
| `git.workflow.branchPrefix` | `volundr/session` | Branch name prefix |
| `git.workflow.protectMain` | `true` | Protect main/master from direct commits |
| `git.workflow.defaultMergeMethod` | `squash` | Merge method (merge, squash, rebase) |
| `git.workflow.autoMergeThreshold` | `0.9` | Confidence threshold for auto-merge |
| `git.workflow.notifyMergeThreshold` | `0.6` | Confidence threshold for notify-then-merge |

---

## Envoy Sidecar

JWT-validating reverse proxy. Extracts claims into trusted headers for the identity adapter.

| Key | Default | Description |
|-----|---------|-------------|
| `envoy.enabled` | `false` | Enable Envoy sidecar |
| `envoy.image.repository` | `envoyproxy/envoy` | Envoy image |
| `envoy.image.tag` | `v1.32-latest` | Envoy version |
| `envoy.port` | `8443` | Listener port |
| `envoy.adminPort` | `9901` | Admin port (localhost only) |
| `envoy.connectTimeout` | `0.25s` | Upstream connect timeout |
| `envoy.upstreamTimeout` | `60s` | Upstream request timeout |

### JWT Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `envoy.jwt.enabled` | `false` | Enable JWT filter |
| `envoy.jwt.issuer` | `""` | JWT issuer URL |
| `envoy.jwt.audiences` | `[]` | Allowed audiences |
| `envoy.jwt.jwksUri` | `""` | JWKS endpoint |
| `envoy.jwt.jwksTimeout` | `5s` | JWKS fetch timeout |
| `envoy.jwt.jwksCacheDurationSeconds` | `300` | JWKS cache TTL |
| `envoy.jwt.tenantClaim` | `tenant_id` | Claim for tenant ID |
| `envoy.jwt.rolesClaim` | `resource_access.volundr.roles` | Claim for roles (dot notation) |
| `envoy.jwt.keycloakHost` | `""` | IDP upstream host for JWKS |
| `envoy.jwt.keycloakPort` | `8080` | IDP upstream port |
| `envoy.jwt.keycloakTls` | `false` | TLS for IDP upstream |
| `envoy.jwt.bypassPrefixes` | `/api/v1/auth/config`, `/health` | Paths that skip JWT validation |
| `envoy.jwt.extraClaimHeaders` | `[]` | Additional claim-to-header mappings |

### Header Names

| Key | Default | Description |
|-----|---------|-------------|
| `envoy.headerNames.userId` | `x-auth-user-id` | User ID header |
| `envoy.headerNames.email` | `x-auth-email` | Email header |
| `envoy.headerNames.tenant` | `x-auth-tenant` | Tenant header |
| `envoy.headerNames.roles` | `x-auth-roles` | Roles header |

---

## Identity

| Key | Default | Description |
|-----|---------|-------------|
| `identity.adapter` | `...AllowAllIdentityAdapter` | Identity adapter class |
| `identity.kwargs` | `{}` | Adapter constructor kwargs |
| `identity.secretKwargs` | `[]` | Kwargs from K8s secrets |
| `identity.roleMapping.admin` | `volundr:admin` | IDP role mapping for admin |
| `identity.roleMapping.developer` | `volundr:developer` | IDP role mapping for developer |
| `identity.roleMapping.viewer` | `volundr:viewer` | IDP role mapping for viewer |

AllowAll (development):

```yaml
identity:
  adapter: "volundr.adapters.outbound.identity.AllowAllIdentityAdapter"
```

Envoy trusted headers (production):

```yaml
identity:
  adapter: "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"
  kwargs:
    user_id_header: "x-auth-user-id"
    email_header: "x-auth-email"
    tenant_header: "x-auth-tenant"
    roles_header: "x-auth-roles"
```

---

## Authorization

| Key | Default | Description |
|-----|---------|-------------|
| `authorization.adapter` | `...AllowAllAuthorizationAdapter` | Authorization adapter class |
| `authorization.kwargs` | `{}` | Adapter constructor kwargs |
| `authorization.secretKwargs` | `[]` | Kwargs from K8s secrets |

AllowAll (development):

```yaml
authorization:
  adapter: "volundr.adapters.outbound.authorization.AllowAllAuthorizationAdapter"
```

Simple role-based:

```yaml
authorization:
  adapter: "volundr.adapters.outbound.authorization.SimpleRoleAuthorizationAdapter"
```

Cerbos PDP:

```yaml
authorization:
  adapter: "volundr.adapters.outbound.cerbos.CerbosAuthorizationAdapter"
  kwargs:
    url: "http://cerbos:3592"
    timeout: 5
```

---

## Credential Store

| Key | Default | Description |
|-----|---------|-------------|
| `credentialStore.adapter` | `...MemoryCredentialStore` | Credential store adapter class |
| `credentialStore.kwargs` | `{}` | Adapter constructor kwargs |
| `credentialStore.secretKwargs` | `[]` | Kwargs from K8s secrets |

Memory (development):

```yaml
credentialStore:
  adapter: "volundr.adapters.outbound.memory_credential_store.MemoryCredentialStore"
```

Vault / OpenBao:

```yaml
credentialStore:
  adapter: "volundr.adapters.outbound.vault_credential_store.VaultCredentialStore"
  kwargs:
    url: "http://vault:8200"
    auth_method: "kubernetes"
    mount_path: "secret"
```

Infisical:

```yaml
credentialStore:
  adapter: "volundr.adapters.outbound.infisical_credential_store.InfisicalCredentialStore"
  kwargs:
    site_url: "https://app.infisical.com"
    client_id: ""
    client_secret: ""
    project_id: ""
```

---

## Secret Injection

| Key | Default | Description |
|-----|---------|-------------|
| `secretInjection.adapter` | `...InMemorySecretInjectionAdapter` | Secret injection adapter class |
| `secretInjection.kwargs` | `{}` | Adapter constructor kwargs |
| `secretInjection.secretKwargs` | `[]` | Kwargs from K8s secrets |

InMemory (development, no-op):

```yaml
secretInjection:
  adapter: "volundr.adapters.outbound.memory_secret_injection.InMemorySecretInjectionAdapter"
```

Infisical CSI:

```yaml
secretInjection:
  adapter: "volundr.adapters.outbound.infisical_secret_injection.InfisicalCSISecretInjectionAdapter"
  kwargs:
    infisical_url: "https://infisical.example.com"
```

---

## Storage

### PVCs

| Key | Default | Description |
|-----|---------|-------------|
| `storage.sessions.enabled` | `true` | Enable sessions PVC |
| `storage.sessions.storageClass` | `longhorn` | Storage class |
| `storage.sessions.accessMode` | `ReadWriteMany` | Access mode (must be RWX) |
| `storage.sessions.size` | `1Gi` | PVC size |
| `storage.sessions.mountPath` | `/volundr/sessions` | Mount path |
| `storage.home.enabled` | `true` | Enable home PVC |
| `storage.home.storageClass` | `longhorn` | Storage class |
| `storage.home.accessMode` | `ReadWriteMany` | Access mode (must be RWX) |
| `storage.home.size` | `1Gi` | PVC size |
| `storage.home.mountPath` | `/volundr/home` | Mount path |

### Storage Adapter

| Key | Default | Description |
|-----|---------|-------------|
| `storageAdapter.adapter` | `...InMemoryStorageAdapter` | Storage adapter class |
| `storageAdapter.kwargs` | `{}` | Adapter constructor kwargs |

Kubernetes PVC adapter:

```yaml
storageAdapter:
  adapter: "volundr.adapters.outbound.k8s_storage_adapter.K8sStorageAdapter"
  kwargs:
    namespace: "skuld"
    home_storage_class: "longhorn"
    workspace_storage_class: "longhorn"
    workspace_size_gb: 2
```

---

## Resource Provider

| Key | Default | Description |
|-----|---------|-------------|
| `resourceProvider.adapter` | `...StaticResourceProvider` | Resource provider adapter class |
| `resourceProvider.kwargs` | `{}` | Adapter constructor kwargs |

Static (default):

```yaml
resourceProvider:
  adapter: "volundr.adapters.outbound.static_resource_provider.StaticResourceProvider"
```

Kubernetes device-plugin (GPU discovery):

```yaml
resourceProvider:
  adapter: "volundr.adapters.outbound.k8s_resource_provider.K8sResourceProvider"
  kwargs:
    namespace: "volundr-sessions"
```

---

## Gateway

| Key | Default | Description |
|-----|---------|-------------|
| `gateway.adapter` | `...InMemoryGatewayAdapter` | Gateway adapter class |
| `gateway.kwargs` | `{}` | Adapter constructor kwargs |

Kubernetes Gateway API:

```yaml
gateway:
  adapter: "volundr.adapters.outbound.k8s_gateway.K8sGatewayAdapter"
  kwargs:
    namespace: "volundr"
    gateway_name: "volundr-gateway"
    gateway_namespace: "volundr"
    gateway_domain: "sessions.valhalla.asgard.niuu.world"
    issuer_url: "https://idp.example.com"
    audience: "volundr"
    jwks_uri: "https://idp.example.com/.well-known/jwks"
```

### Session Gateway Resource

| Key | Default | Description |
|-----|---------|-------------|
| `sessionGateway.enabled` | `false` | Enable shared Gateway resource |
| `sessionGateway.name` | `volundr-gateway` | Gateway resource name |
| `sessionGateway.gatewayClassName` | `eg` | GatewayClass (Envoy Gateway default) |
| `sessionGateway.hostname` | `sessions.valhalla.asgard.niuu.world` | HTTPS listener hostname |
| `sessionGateway.certIssuer` | `letsencrypt-prod` | cert-manager ClusterIssuer |
| `sessionGateway.tlsSecretName` | `""` | TLS secret name |
| `sessionGateway.allowedRouteNamespaces` | `All` | Namespace scope for HTTPRoutes |
| `sessionGateway.httpRedirect` | `true` | HTTP to HTTPS redirect |
| `sessionGateway.annotations` | `{}` | Extra annotations |

---

## Session Contributors

Pipeline of contributors that assemble session specs before submission. Runs sequentially in config order. Cleanup runs in reverse.

```yaml
sessionContributors:
  - adapter: "volundr.adapters.outbound.contributors.core.CoreSessionContributor"
    kwargs:
      base_domain: "skuld.valhalla.asgard.niuu.world"
      ingress_enabled: true
  - adapter: "volundr.adapters.outbound.contributors.template.TemplateContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.git.GitContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.integrations.IntegrationContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.storage.StorageContributor"
    kwargs:
      home_enabled: true
  - adapter: "volundr.adapters.outbound.contributors.gateway.GatewayContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.resource.ResourceContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.isolation.IsolationContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.secrets.SecretInjectionContributor"
    kwargs: {}
  - adapter: "volundr.adapters.outbound.contributors.secrets.SecretsContributor"
    kwargs: {}
```

Each entry also supports `secretKwargs` for injecting values from K8s secrets.

---

## Session Definitions

Define session types for workload instantiation.

### skuld-claude

| Key | Default | Description |
|-----|---------|-------------|
| `sessionDefinitions.skuldClaude.enabled` | `true` | Enable Claude session definition |
| `sessionDefinitions.skuldClaude.labels` | `[session]` | Agent routing labels |
| `sessionDefinitions.skuldClaude.active` | `true` | Whether definition is active |
| `sessionDefinitions.skuldClaude.helm.chart` | `skuld` | Chart name |
| `sessionDefinitions.skuldClaude.helm.repo` | `oci://ghcr.io/niuulabs/charts` | Chart repository |
| `sessionDefinitions.skuldClaude.helm.version` | `0.1.0` | Chart version |

Default session values:

| Key | Default |
|-----|---------|
| `defaults.session.model` | `claude-sonnet-4-20250514` |
| `defaults.broker.cliType` | `claude` |
| `defaults.broker.transport` | `sdk` |
| `defaults.broker.skipPermissions` | `true` |
| `defaults.broker.agentTeams` | `false` |
| `defaults.image.repository` | `ghcr.io/niuulabs/skuld` |
| `defaults.image.tag` | `latest` |
| `defaults.resources.requests.memory` | `256Mi` |
| `defaults.resources.requests.cpu` | `100m` |
| `defaults.resources.limits.memory` | `1Gi` |
| `defaults.resources.limits.cpu` | `500m` |
| `defaults.codeServer.enabled` | `true` |
| `defaults.persistence.mountPath` | `/volundr/sessions` |
| `defaults.homeVolume.enabled` | `true` |
| `defaults.securityContext.runAsNonRoot` | `true` |
| `defaults.securityContext.runAsUser` | `1000` |

### skuld-codex

Same structure as skuld-claude with different defaults:

| Key | Default |
|-----|---------|
| `sessionDefinitions.skuldCodex.enabled` | `false` |
| `defaults.session.model` | `o4-mini` |
| `defaults.broker.cliType` | `codex` |
| `defaults.broker.transport` | `subprocess` |
| `defaults.image.repository` | `ghcr.io/niuulabs/skuld` |

---

## Auth Discovery

Public endpoint for CLI auto-configuration.

| Key | Default | Description |
|-----|---------|-------------|
| `authDiscovery.issuer` | `""` | OIDC issuer URL (falls back to gateway issuer) |
| `authDiscovery.cliClientId` | `volundr-cli` | OIDC client ID for CLI |
| `authDiscovery.scopes` | `openid profile email` | OIDC scopes |

---

## Web UI

| Key | Default | Description |
|-----|---------|-------------|
| `web.enabled` | `false` | Enable web UI |
| `web.replicaCount` | `1` | Web UI replicas |
| `web.image.registry` | `ghcr.io` | Web image registry |
| `web.image.repository` | `niuulabs/volundr-web` | Web image repository |
| `web.image.tag` | `""` | Web image tag |
| `web.service.type` | `ClusterIP` | Web service type |
| `web.service.port` | `80` | Web service port |
| `web.ingress.enabled` | `false` | Enable web ingress |
| `web.config.apiBaseUrl` | `""` | API URL (auto-wired if empty) |
| `web.config.oidc.authority` | `""` | OIDC discovery URL |
| `web.config.oidc.clientId` | `""` | OIDC client ID |
| `web.config.oidc.scope` | `openid profile email` | OIDC scopes |

---

## Chronicle

| Key | Default | Description |
|-----|---------|-------------|
| `chronicle.autoCreateOnStop` | `true` | Auto-create chronicle on session stop |
| `chronicle.summaryModel` | `claude-haiku-4-5-20251001` | Model for summaries |
| `chronicle.summaryMaxTokens` | `2000` | Max tokens for summaries |
| `chronicle.retentionDays` | `0` | Retention period (0 = forever) |

---

## Application Config

| Key | Default | Description |
|-----|---------|-------------|
| `config.logLevel` | `info` | Log level (debug, info, warning, error) |
| `config.logFormat` | `json` | Log format (json, text) |
| `config.host` | `0.0.0.0` | Bind host |
| `config.workers` | `4` | Uvicorn workers |
| `config.sessionTimeout` | `3600` | Session timeout in seconds |
| `config.maxSessionsPerUser` | `5` | Max sessions per user |
| `config.corsOrigins` | `*` | CORS allowed origins |
| `config.corsAllowCredentials` | `true` | CORS allow credentials |

---

## Scaling and Availability

### Autoscaling

| Key | Default | Description |
|-----|---------|-------------|
| `autoscaling.enabled` | `false` | Enable HPA |
| `autoscaling.minReplicas` | `1` | Minimum replicas |
| `autoscaling.maxReplicas` | `10` | Maximum replicas |
| `autoscaling.targetCPUUtilizationPercentage` | `80` | CPU target |
| `autoscaling.targetMemoryUtilizationPercentage` | `""` | Memory target |
| `autoscaling.customMetrics` | `[]` | Custom metrics |
| `autoscaling.behavior` | `{}` | Scaling behavior |

### Pod Disruption Budget

| Key | Default | Description |
|-----|---------|-------------|
| `podDisruptionBudget.enabled` | `false` | Enable PDB |
| `podDisruptionBudget.minAvailable` | `1` | Min available pods |
| `podDisruptionBudget.maxUnavailable` | `""` | Max unavailable pods |

---

## Resources

API backend:

```yaml
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 1000m
    memory: 1Gi
```

---

## Security

### Pod Security Context

```yaml
podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 1000
  seccompProfile:
    type: RuntimeDefault
```

### Container Security Context

```yaml
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  runAsUser: 1000
  capabilities:
    drop:
      - ALL
```

### Network Policy

| Key | Default | Description |
|-----|---------|-------------|
| `networkPolicy.enabled` | `false` | Enable NetworkPolicy |
| `networkPolicy.ingressFrom` | `[]` | Ingress selectors |
| `networkPolicy.databasePodSelector` | `{}` | DB pod selector for egress |
| `networkPolicy.extraIngress` | `[]` | Extra ingress rules |
| `networkPolicy.extraEgress` | `[]` | Extra egress rules |

---

## Scheduling

| Key | Default | Description |
|-----|---------|-------------|
| `nodeSelector` | `{}` | Node selector |
| `tolerations` | `[]` | Tolerations |
| `affinity` | `{}` | Affinity rules |
| `topologySpreadConstraints` | `[]` | Topology spread |
| `priorityClassName` | `""` | Priority class |
| `terminationGracePeriodSeconds` | `30` | Graceful shutdown timeout |

---

## Integrations

Define integrations available for users to attach to sessions.

```yaml
integrations:
  definitions:
    - slug: linear
      name: Linear
      description: "Linear issue tracker integration"
      integration_type: issue_tracker
      adapter: "volundr.adapters.outbound.integrations.linear.LinearAdapter"
      icon: "linear"
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

---

## Migrations

| Key | Default | Description |
|-----|---------|-------------|
| `migrations.enabled` | `true` | Run migrations via init container |
| `migrations.image.repository` | `migrate/migrate` | Migration image |
| `migrations.image.tag` | `v4.17.0` | Migration version |

---

## Existing Secrets

| Key | Default | Description |
|-----|---------|-------------|
| `existingSecrets.anthropic` | `volundr-anthropic-api` | Secret with `ANTHROPIC_API_KEY` |
