# Skuld Helm Chart

![Version: 0.55.0](https://img.shields.io/badge/Version-0.55.0-informational?style=flat-square)
![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square)
![AppVersion: 0.55.0](https://img.shields.io/badge/AppVersion-0.55.0-informational?style=flat-square)

Helm chart for deploying **Skuld** -- a Claude Code CLI session pod with a WebSocket broker and terminal/local-service sidecars.

## Overview

Skuld is the session runtime for [Volundr](../volundr/). Each session deploys as a multi-container pod with:

- **Nginx** -- single entry point routing all traffic to internal containers
- **Skuld broker** -- WebSocket bridge between Claude Code CLI and the AI model
- **Devrunner** (optional) -- terminal access and dynamic local service management
- **Envoy** (optional) -- JWT validation and header extraction sidecar

```
                    ┌─────────────────────────────────────────────┐
                    │                  Skuld Pod                  │
                    │                                             │
  Ingress/Gateway   │  ┌─────────┐   /session   ┌─────────────┐ │
  ─────────────────►│  │  nginx  │──────────────►│ skuld broker│ │
                    │  │  :8080  │   /api/       │    :8081    │ │
                    │  │         │───────────────►│             │ │
                    │  │         │   /terminal   ┌─────────────┐ │
                    │  │         │──────────────►│  devrunner  │ │
                    │  └─────────┘               │    :7681    │ │
                    └─────────────────────────────────────────────┘
```

## Prerequisites

- Kubernetes 1.24+
- Helm 3.8+
- A PersistentVolumeClaim for session workspace storage
- API key secret for the AI provider (Anthropic or OpenAI)
- (Optional) cert-manager for automatic TLS
- (Optional) Envoy Gateway for Gateway API routing

## How Sessions Are Deployed

Skuld is **not installed manually**. It is deployed automatically by the Volundr control plane:

1. A user creates a session in Volundr
2. Volundr creates a Flux HelmRelease for the session
3. Flux deploys a Skuld Helm release with session-specific values
4. The session pod starts, clones the git repo (if configured), and accepts connections
5. On session stop, the pod generates a chronicle summary and is deleted

The session definitions in the Volundr chart define default values for Skuld deployments. Per-session overrides (model, repo, resources) are merged at deployment time.

## Installation (Manual / Development)

For development or testing, you can deploy Skuld directly:

```bash
# Create required secrets
kubectl create secret generic anthropic-api-key \
  --from-literal=api-key=your-api-key

# Create workspace PVC
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: volundr-sessions
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 5Gi
EOF

# Install with a session ID
helm install my-session ./charts/skuld \
  --set session.id=dev-session-001 \
  --set session.name="Dev Session" \
  --set ingress.host=dev-session.example.com
```

## Values

<!-- README VALUES TABLE -- generated from values.yaml -->
<!-- To regenerate: run `charts/skuld/README-generate.sh` -->

### Global Parameters

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `global.imagePullSecrets` | list | `[]` | Global image pull secrets (list of secret names) |
| `global.image.repository` | string | `""` | Global image repository override |
| `global.image.tag` | string | `""` | Global image tag override |

### Session

These values are typically set by Volundr at deployment time, not by the user.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `session.id` | string | `""` | Session identifier (UUID, set by Volundr) |
| `session.name` | string | `""` | Human-readable session name |
| `session.model` | string | `"claude-sonnet-4-20250514"` | AI model to use for the session |

### Image (Skuld Broker)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `image.repository` | string | `"ghcr.io/niuulabs/skuld"` | Skuld broker image repository |
| `image.tag` | string | `"latest"` | Skuld broker image tag |
| `image.pullPolicy` | string | `"Always"` | Image pull policy |

### Resources (Skuld Broker)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `resources.requests.memory` | string | `"256Mi"` | Broker memory request |
| `resources.requests.cpu` | string | `"100m"` | Broker CPU request |
| `resources.limits.memory` | string | `"1Gi"` | Broker memory limit |
| `resources.limits.cpu` | string | `"500m"` | Broker CPU limit |

### Nginx (Traffic Router)

Nginx is the single entry point for all traffic. It routes requests to the appropriate container based on URL path.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `nginx.image.repository` | string | `"nginx"` | Nginx image repository |
| `nginx.image.tag` | string | `"alpine"` | Nginx image tag |
| `nginx.image.pullPolicy` | string | `"IfNotPresent"` | Nginx image pull policy |
| `nginx.resources.requests.memory` | string | `"32Mi"` | Nginx memory request |
| `nginx.resources.requests.cpu` | string | `"10m"` | Nginx CPU request |
| `nginx.resources.limits.memory` | string | `"128Mi"` | Nginx memory limit |
| `nginx.resources.limits.cpu` | string | `"100m"` | Nginx CPU limit |

### Local Services (Devrunner)

Optional sidecar for terminal access and dynamic local service management. Provides a WebSocket-based shell and port forwarding for services running inside the session.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `localServices.enabled` | bool | `true` | Enable local service management |
| `localServices.portRange.start` | int | `9001` | Start of dynamic port range |
| `localServices.portRange.end` | int | `9020` | End of dynamic port range |
| `localServices.terminal.port` | int | `7681` | Terminal WebSocket port |
| `localServices.terminal.restricted` | bool | `false` | Run the terminal in restricted-shell mode |
| `localServices.terminal.allowedCommands` | string | `""` | Override allowed commands in restricted mode (comma-separated, empty = use defaults) |
| `localServices.terminal.debug` | bool | `false` | Enable debug UI at `/debug/` (xterm.js test page) |
| `localServices.devrunner.image.repository` | string | `"ghcr.io/niuulabs/devrunner"` | Devrunner image repository |
| `localServices.devrunner.image.tag` | string | `"latest"` | Devrunner image tag |
| `localServices.devrunner.image.pullPolicy` | string | `"Always"` | Devrunner image pull policy |
| `localServices.devrunner.resources.requests.memory` | string | `"512Mi"` | Devrunner memory request |
| `localServices.devrunner.resources.requests.cpu` | string | `"100m"` | Devrunner CPU request |
| `localServices.devrunner.resources.limits.memory` | string | `"4Gi"` | Devrunner memory limit |
| `localServices.devrunner.resources.limits.cpu` | string | `"2000m"` | Devrunner CPU limit |

### Service

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `service.type` | string | `"ClusterIP"` | Service type |
| `service.port` | int | `8080` | Single entry point port (nginx) |

### Ingress

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `ingress.enabled` | bool | `true` | Enable ingress |
| `ingress.className` | string | `"traefik"` | Ingress class name |
| `ingress.annotations` | object | See values.yaml | Ingress annotations (controller-specific). Default includes cert-manager cluster-issuer |
| `ingress.host` | string | `""` | Hostname for the session ingress (set by Volundr, e.g., `session-uuid.niuu.world`) |
| `ingress.paths.session` | string | `"/session"` | WebSocket path for Skuld broker |
| `ingress.paths.ide` | string | `"/"` | Default catch-all path forwarded to the broker |
| `ingress.tls.enabled` | bool | `true` | Enable TLS |
| `ingress.tls.secretName` | string | `""` | TLS secret name (auto-generated from release name if empty) |

### Persistence (Workspace)

Shared PVC for session workspace data. Each session creates its workspace under `/volundr/sessions/{session-id}/workspace/`.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `persistence.enabled` | bool | `true` | Enable workspace persistence |
| `persistence.existingClaim` | string | `"volundr-sessions"` | Name of an existing PVC to use |
| `persistence.mountPath` | string | `"/volundr/sessions"` | Mount path for the sessions PVC |

### Home Volume (Persistent User Config)

Persistent home directory shared across all sessions for a given user. Stores CLI config (`.claude/`, `.codex/`) and shell config. Credential files are mounted from a K8s Secret and symlinked into `$HOME/<destDir>/` so they auto-update when the secret changes (~60s kubelet sync).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `homeVolume.enabled` | bool | `false` | Enable persistent home volume |
| `homeVolume.existingClaim` | string | `""` | Name of an existing PVC to use for the home directory |
| `homeVolume.mountPath` | string | `"/volundr/home"` | Mount path for the home PVC |
| `homeVolume.credentialFiles.secretName` | string | `""` | Pre-existing K8s secret containing CLI credential files |
| `homeVolume.credentialFiles.destDir` | string | `".claude"` | Subdirectory under mountPath where credential symlinks are placed (e.g., `.claude` for Claude Code, `.codex` for OpenAI Codex) |
| `homeVolume.credentialFiles.secretMountPath` | string | `"/volundr/secrets/credential-files"` | Internal mount path for the credentials secret volume |

### Git

Optional git repository cloned on session start via an init container. Each session clones into its own workspace directory, so multiple sessions with different repos can coexist on the same PVC.

Forge sessions use an attached source-control integration from Settings. OpenShell uses its attached dynamic credential provider for clone and subsequent Git requests, without copying the token into the sandbox. For plain Kubernetes (Helm or direct), its token is injected into a session-mounted file; `git.credentials.tokenFile` takes precedence over any cluster `secretName`. The clone step installs a repository-scoped credential helper that reads this file for each fetch or push, including operations from Skuld, extra sidecars, and the terminal. Tokens are not stored in Helm values or repository URLs. Missing integration credentials fail startup with an actionable error.

Commit identity defaults to the session owner's email when Git has no identity configured. Existing repository identity is preserved. When a saved selection contains no source-control integration, startup automatically attaches the owner's enabled source-control integrations, including ones added after the session was created. An explicit source-control selection is preserved. The token needs Contents read access to clone and write access to push.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `git.repoUrl` | string | `""` | Git repository URL to clone (empty = no clone) |
| `git.branch` | string | `""` | Branch to checkout (empty = default branch) |
| `git.credentials.tokenFile` | string | `""` | Injected integration token file used for cloning and runtime Git operations |
| `git.userName` | string | `""` | Commit author name when not already configured |
| `git.userEmail` | string | `""` | Commit author email when not already configured |
| `git.credentials.secretName` | string | `""` | Name of a Kubernetes secret containing a git token |
| `git.credentials.tokenKey` | string | `"token"` | Key within the secret that holds the token |
| `git.credentials.username` | string | `"x-access-token"` | Username for HTTPS auth (default works with GitHub PATs and deploy tokens) |
| `git.image.repository` | string | `"alpine/git"` | Git init container image repository |
| `git.image.tag` | string | `"latest"` | Git init container image tag |
| `git.image.pullPolicy` | string | `"IfNotPresent"` | Git init container image pull policy |

### Broker

Core Skuld broker configuration controlling which AI CLI backend to use and how it communicates.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `broker.transportAdapter` | string | `"skuld.transports.sdk.SDKTransport"` | Fully-qualified class path of the transport adapter. Replaces `broker.cliType` + `broker.transport` with a single, extensible field |
| `broker.cliBinary` | string | `"claude"` | CLI executable used by subprocess-based transports |
| `broker.remoteControlPermissionMode` | string | `""` | Claude Remote Control permission mode; empty follows `broker.skipPermissions` |
| `broker.maxPresentedFileBytes` | int | `52428800` | Maximum file size accepted by the present-file endpoint |
| `broker.cliType` | string | `"claude"` | **(DEPRECATED)** AI CLI backend: `"claude"` (Claude Code) or `"codex"` (OpenAI Codex). Use `broker.transportAdapter` instead |
| `broker.transport` | string | `"sdk"` | **(DEPRECATED)** CLI transport mode: `"sdk"` (WebSocket, default) or `"subprocess"` (legacy). Use `broker.transportAdapter` instead |
| `broker.skipPermissions` | bool | `true` | Skip tool permission prompts and use full access (Claude `bypassPermissions`; Codex `approvalPolicy=never` with `danger-full-access`) |
| `broker.agentTeams` | bool | `false` | Enable Claude Code experimental Agent Teams (Claude only) |

### Volundr API

Configuration for reporting token usage and session events back to the Volundr control plane.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `volundr.apiUrl` | string | `""` | Envoy-routed Volundr API origin for session event, trace, and usage reporting |
| `volundr.serviceUserId` | string | `"skuld-broker"` | Service identity header for control-plane callbacks (`x-auth-user-id`) |
| `volundr.serviceTenantId` | string | `"default"` | Tenant header for control-plane callbacks (`x-auth-tenant`) |
| `volundr.externalApiTokenSecret.name` | string | `""` | Existing Kubernetes Secret containing the outbound service token; empty disables token injection |
| `volundr.externalApiTokenSecret.key` | string | `"token"` | Key within the referenced Secret |

### Secret requests

An agent asks the user for a secret with `request-secret` or the Forge MCP `request_secret` tool. The user types it into a masked card in the chat; it reaches the agent only as a private file path. The wait is bounded by the engine's MCP call timeouts, so `secretRequests.waitMaxS` must stay below `forgeMcp.brokerRequestTimeoutS` and `forgeMcp.toolTimeoutS`; the broker refuses to start otherwise. When the wait passes, the call fails loudly and the request stays open in the chat for the agent to ask again.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `secretRequests.pollIntervalS` | int | `2` | Seconds between checks of whether the user answered the card (`secret_requests.poll_interval_s`) |
| `secretRequests.waitMaxS` | int | `50` | Longest one `request_secret` call waits for the user, in seconds (`secret_requests.wait_max_s`) |
| `ask.pollIntervalS` | int | `2` | Seconds between checks of whether an `ask` recipient is blocked on a person or gone (`ask.poll_interval_s`) |
| `ask.waitMaxS` | int | `50` | Longest one `ask` call waits, and the ceiling for a caller's `deadline_s`, in seconds (`ask.wait_max_s`); must stay below `forgeMcp.brokerRequestTimeoutS` and `forgeMcp.toolTimeoutS` |
| `forgeMcp.brokerRequestTimeoutS` | number | `null` | Seconds the engine allows one stdio-server to broker tool call (`forge_mcp.broker_request_timeout_s`); null keeps the broker default of 60 |
| `forgeMcp.toolTimeoutS` | number | `null` | Seconds the engine allows one MCP tool call (`forge_mcp.tool_timeout_s`), set for both Codex (`tool_timeout_sec`) and Claude Code (per-server `timeout`); null keeps the broker default of 120 |

### Mesh

A session joins a flock as a mesh peer when `mesh.enabled` is true: it announces itself (persona, role, description, capabilities, the topics it consumes and produces, its activity), receives what peers send into its inbox, and gets the `mesh_send`, `mesh_publish` and `mesh_peers` session tools. Every value below renders into the `mesh` section of the broker's config file under the key shown; a value left `null` (or empty) keeps Skuld's own default, so a chart that sets none of them renders `enabled: false` and `transport: "nng"` exactly as Skuld defaults. Forge hands these to a session pod as it does every other value of this chart: the pod manager's `session_defaults` (the volundr chart's `podManager.kwargs`) and the session contributors merge into the release values.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mesh.enabled` | bool | `false` | Join the flock as a mesh peer (`mesh.enabled`) |
| `mesh.transport` | string | `"nng"` | Mesh transport: `nng`, `nats`, `in_process` or a Sleipnir transport class path (`mesh.transport`) |
| `mesh.peerPorts` | list | `[]` | Container ports to expose for direct mesh peers; not a Skuld setting |
| `mesh.peerId` | string | `""` | This peer's id (`mesh.peer_id`); empty uses the pod's host name |
| `mesh.realmId` | string | `""` | Realm the peer announces in and discovers within (`mesh.realm_id`) |
| `mesh.persona` | string | `null` | Persona name announced when no persona is pinned (`mesh.persona`); null keeps `coder` |
| `mesh.role` | string | `""` | What the session is for, announced so peers can choose (`mesh.role`); empty announces the pinned persona's id |
| `mesh.description` | string | `""` | What the session does, announced to peers (`mesh.description`); the pinned persona's own description wins |
| `mesh.capabilities` | list | `null` | Capabilities announced (`mesh.capabilities`) |
| `mesh.tools` | list | `null` | Tools announced (`mesh.tools`) |
| `mesh.consumesEventTypes` | list | `null` | Topics subscribed to (`mesh.consumes_event_types`); null keeps `["code.requested"]`; a pinned persona's `consumes` are added |
| `mesh.publishEventTypes` | list | `null` | Topics `mesh_publish` may publish (`mesh.publish_event_types`), added to the pinned persona's `produces`; any other topic is refused |
| `mesh.publishedEventUrgency` | number | `null` | Attention hint 0.0-1.0 on published events (`mesh.published_event_urgency`); null keeps 0.8 |
| `mesh.nng.pubSubAddress` | string | `""` | NNG publish/subscribe address (`mesh.nng.pub_sub_address`) |
| `mesh.nng.reqRepAddress` | string | `""` | NNG request/reply address (`mesh.nng.req_rep_address`) |
| `mesh.adapters` | list | `null` | Mesh adapter entries (`mesh.adapters`): an `adapter` alias or class path plus that adapter's settings |
| `mesh.discoveryAdapters` | list | `null` | Discovery adapter entries (`mesh.discovery_adapters`); with none, `mesh_peers` is an error |
| `mesh.nats` | map | `{}` | NATS JetStream settings (`mesh.nats`), keys of `niuu.mesh.config.MeshNatsConfig` |
| `mesh.rpcTimeoutS` | number | `null` | Seconds a mesh request waits for the peer's answer (`mesh.rpc_timeout_s`); null keeps 10 |
| `mesh.rpcReplyCacheSize` | int | `null` | Replies remembered so a retried request is answered once (`mesh.rpc_reply_cache_size`) |
| `mesh.diffMaxBytes` | int | `null` | Largest diff a peer may fetch (`mesh.diff_max_bytes`); null keeps 8192 |
| `mesh.diffTimeoutS` | number | `null` | Seconds a diff request may take (`mesh.diff_timeout_s`); null keeps 10 |

Setting `mesh.enabled` or `mesh.transport` had no effect on the broker before these values were rendered; only `mesh.peerPorts` did.

The `skuld-planner` chart renders the same section with the same template (`templates/_mesh-config.tpl`, symlinked into the planner chart) and has the same `mesh` values, so the two cannot drift; a test compares them.

### Environment Variables

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `envSecrets` | list | See below | Secrets injected as environment variables into the broker container. Each entry maps one key from a K8s Secret to an env var: `{envVar, secretName, secretKey}`. Default injects `ANTHROPIC_API_KEY` |
| `claude.disableAutoUpdate` | bool | `true` | Sets `DISABLE_AUTOUPDATER=1` so Claude Code never updates itself in a cluster session (the CLI is pinned in the image; a self-update restarts it mid-start-up). Set independently of `envVars`. |
| `envVars` | list | `[]` | Plain environment variables injected into the broker container. Use for non-secret configuration like proxy URLs. Each entry: `{name, value}` |

**Default envSecrets:**

```yaml
envSecrets:
  - envVar: ANTHROPIC_API_KEY
    secretName: anthropic-api-key
    secretKey: api-key
```

<details>
<summary>envVars example (Anthropic proxy)</summary>

```yaml
envVars:
  - name: ANTHROPIC_BASE_URL
    value: "https://proxy.example.com"
  - name: ANTHROPIC_AUTH_TOKEN
    value: "bearer-token"
```

</details>

### Envoy Sidecar Proxy

Optional Envoy sidecar for JWT validation and header extraction. When enabled, Envoy becomes the service target and proxies to nginx.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `envoy.enabled` | bool | `false` | Enable Envoy sidecar proxy |
| `envoy.image.repository` | string | `"envoyproxy/envoy"` | Envoy image repository |
| `envoy.image.tag` | string | `"v1.32-latest"` | Envoy image tag |
| `envoy.image.pullPolicy` | string | `"IfNotPresent"` | Envoy image pull policy |
| `envoy.port` | int | `8444` | Envoy listener port (becomes the service target when enabled) |
| `envoy.adminPort` | int | `9901` | Envoy admin interface port (localhost only) |
| `envoy.connectTimeout` | string | `"0.25s"` | Upstream connect timeout |
| `envoy.upstreamTimeout` | string | `"3600s"` | Upstream request timeout (long for WebSocket sessions) |
| `envoy.resources.requests.cpu` | string | `"50m"` | CPU request |
| `envoy.resources.requests.memory` | string | `"64Mi"` | Memory request |
| `envoy.resources.limits.cpu` | string | `"200m"` | CPU limit |
| `envoy.resources.limits.memory` | string | `"128Mi"` | Memory limit |
| `envoy.headerNames.userId` | string | `"x-auth-user-id"` | Header name for user ID |
| `envoy.headerNames.email` | string | `"x-auth-email"` | Header name for email |
| `envoy.headerNames.tenant` | string | `"x-auth-tenant"` | Header name for tenant |
| `envoy.headerNames.roles` | string | `"x-auth-roles"` | Header name for roles |
| `envoy.jwt.enabled` | bool | `false` | Enable JWT authentication filter |
| `envoy.jwt.issuer` | string | `""` | JWT issuer URL (Keycloak realm) |
| `envoy.jwt.audiences` | list | `[]` | Allowed JWT audiences |
| `envoy.jwt.jwksUri` | string | `""` | JWKS URI for key retrieval |
| `envoy.jwt.jwksTimeout` | string | `"5s"` | JWKS fetch timeout |
| `envoy.jwt.jwksCacheDurationSeconds` | int | `300` | JWKS cache duration in seconds |
| `envoy.jwt.tenantClaim` | string | `"tenant_id"` | JWT claim containing tenant ID |
| `envoy.jwt.rolesClaim` | string | `"resource_access.volundr.roles"` | JWT claim containing roles |
| `envoy.jwt.keycloakHost` | string | `""` | Keycloak upstream host |
| `envoy.jwt.keycloakPort` | int | `8080` | Keycloak upstream port |
| `envoy.jwt.keycloakTls` | bool | `false` | Enable TLS for Keycloak upstream |
| `envoy.jwt.extraClaimHeaders` | list | `[]` | Additional JWT claim-to-header mappings. Each entry: `{headerName, claimName}` |
| `envoy.extraHttpFilters` | list | `[]` | Extra HTTP filters (inserted before the router filter) |
| `envoy.extraClusters` | list | `[]` | Extra upstream clusters |

### Gateway API

Alternative to Ingress for session routing. When enabled, creates an `HTTPRoute` and optional `SecurityPolicy` per session. Routes are attached to a shared `Gateway` resource managed by the Volundr chart.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `gateway.enabled` | bool | `false` | Enable Gateway API HTTPRoute for this session |
| `gateway.name` | string | `"volundr-gateway"` | Name of the shared Gateway resource |
| `gateway.namespace` | string | `"volundr-system"` | Namespace where the shared Gateway lives |
| `gateway.userId` | string | `""` | User ID label for the HTTPRoute (owner of the session) |
| `gateway.cors.allowOrigins` | list | `["*"]` | Allowed origins for CORS |
| `gateway.cors.allowMethods` | list | `["GET","POST","OPTIONS"]` | Allowed HTTP methods |
| `gateway.cors.allowHeaders` | list | `["Authorization","Content-Type"]` | Allowed request headers |
| `gateway.cors.allowCredentials` | bool | `true` | Allow credentials (cookies, auth headers) |
| `gateway.jwt.enabled` | bool | `false` | Enable JWT SecurityPolicy for this session's HTTPRoute |
| `gateway.jwt.issuer` | string | `""` | JWT issuer URL |
| `gateway.jwt.audiences` | list | `[]` | Allowed JWT audiences |
| `gateway.jwt.jwksUri` | string | `""` | JWKS URI for key retrieval |

### Pod Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `podLabels` | object | `{}` | Extra labels applied to session pods (e.g., `volundr/owner` for PVC isolation) |
| `nodeSelector` | object | `{}` | Node selector for scheduling |
| `tolerations` | list | `[]` | Tolerations for scheduling |
| `affinity` | object | `{}` | Affinity rules for scheduling |
| `runtimeClassName` | string | `""` | Runtime class name for the pod (e.g., `"nvidia"`, `"kata"`, `"gvisor"`) |

### Security Context

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `securityContext.runAsNonRoot` | bool | `true` | Run as non-root user |
| `securityContext.runAsUser` | int | `1000` | Run as user ID |
| `securityContext.fsGroup` | int | `1000` | Filesystem group ID |
| `securityContext.fsGroupChangePolicy` | string | `"OnRootMismatch"` | When the kubelet re-owns mounted volumes. `Always` walks every file on each pod start, which took over two minutes on a user home of 250k files. |

## Container Architecture

### Traffic Flow

```
External → Ingress/Gateway → Service (:8080) → nginx → backend containers
```

When Envoy is enabled:
```
External → Ingress/Gateway → Service (:8444) → Envoy → nginx → backend containers
```

### Nginx Routing Rules

| Path | Backend | Description |
|------|---------|-------------|
| `/session` | Skuld broker (:8081) | WebSocket connection for Claude Code CLI |
| `/api/` | Skuld broker (:8081) | REST API endpoints |
| `/health`, `/ready` | Skuld broker (:8081) | Health and readiness probes |
| `/terminal/` | Devrunner (:7681) | WebSocket terminal (if localServices enabled) |
| `/debug/` | Static HTML | xterm.js debug UI (if terminal.debug enabled) |
| `/svc/` | Dynamic | Local services from `.services/nginx.conf` |
| `/` | Skuld broker (:8081) | Default catch-all route |

### Init Containers

| Container | Condition | Description |
|-----------|-----------|-------------|
| `home-setup` | `homeVolume.enabled` | Creates home directory structure on NFS volumes, symlinks credential files from K8s secret |
| `git-clone` | `git.repoUrl` set | Clones git repository into workspace using `git init` + `fetch` + `checkout` (works with non-empty dirs) |

### Dynamic Service Discovery

Services running inside the session pod can register themselves by writing to `.services/nginx.conf` in the workspace directory. Nginx includes this file and routes `/svc/` prefixed requests to registered services. The devrunner container manages the port range (`9001-9020` by default).

## Secrets

### Required

| Secret Name | Keys | Description |
|-------------|------|-------------|
| `anthropic-api-key` (configurable) | `api-key` | Anthropic API key. Injected as `ANTHROPIC_API_KEY` env var |

### Optional

| Secret Name | Keys | Description |
|-------------|------|-------------|
| Git credentials (configurable) | `token` | Git token for cloning private repositories |
| Credential files (configurable) | (files) | CLI credential files symlinked into home directory |

## Example Configurations

### Claude Code Session (default)

```yaml
session:
  id: "my-session-001"
  name: "Feature Development"
  model: "claude-sonnet-4-20250514"

broker:
  transportAdapter: "skuld.transports.sdk_websocket.SdkWebSocketTransport"
  skipPermissions: false

ingress:
  host: "my-session.dev.example.com"

git:
  repoUrl: "https://github.com/org/repo.git"
  branch: "main"
  credentials:
    secretName: github-token
```

### OpenAI Codex Session

```yaml
session:
  id: "codex-session-001"
  name: "Codex Development"
  model: "o4-mini"

broker:
  transportAdapter: "skuld.transports.codex_subprocess.CodexSubprocessTransport"
  skipPermissions: false

image:
  repository: ghcr.io/niuulabs/skuld
  tag: "latest"

homeVolume:
  enabled: true
  existingClaim: "volundr-user-abc-home"
  credentialFiles:
    secretName: "codex-credentials"
    destDir: ".codex"

envSecrets:
  - envVar: OPENAI_API_KEY
    secretName: openai-api-key
    secretKey: api-key
```

### Minimal (No IDE, No Terminal)

```yaml
session:
  id: "headless-001"
  model: "claude-sonnet-4-20250514"

codeServer:
  enabled: false

localServices:
  enabled: false

resources:
  requests:
    cpu: "50m"
    memory: "128Mi"
  limits:
    cpu: "250m"
    memory: "512Mi"
```

### With Gateway API and JWT Auth

```yaml
ingress:
  enabled: false

gateway:
  enabled: true
  name: "volundr-gateway"
  namespace: "volundr"
  userId: "user-123"
  jwt:
    enabled: true
    issuer: "https://keycloak.example.com/realms/volundr"
    audiences:
      - volundr
    jwksUri: "https://keycloak.example.com/realms/volundr/protocol/openid-connect/certs"
```

## Troubleshooting

### Check pod status

```bash
kubectl get pods -l app.kubernetes.io/name=skuld
```

### View broker logs

```bash
kubectl logs <pod-name> -c skuld
```

### View nginx logs

```bash
kubectl logs <pod-name> -c nginx
```

### Test WebSocket connection

```bash
kubectl port-forward <pod-name> 8080:8080
wscat -c ws://localhost:8080/session
```

### Test health endpoint

```bash
kubectl port-forward <pod-name> 8080:8080
curl http://localhost:8080/health
```

---

<!-- This README was generated from values.yaml comments using charts/skuld/README-generate.sh -->
