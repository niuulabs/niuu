# OpenShell Runtime

OpenShell mode creates Forge sessions and long-lived resident Ravns through the
OpenShell gateway API. Völundr does not shell out to the OpenShell CLI; it mints
a Keycloak client-credentials token and calls the gateway gRPC service directly.

The Niuu Kubernetes deployment installs SPIRE on every managed cluster except
`local`. SPIRE gives each OpenShell sandbox a workload identity used for dynamic
Provider v2 credential grants.

## Runtime Shape

```text
Völundr
  -> OpenShellGatewayPodManager
  -> OpenShell gateway gRPC API
  -> Kubernetes compute driver
  -> OpenShell Sandbox
  -> Skuld broker (+ Ravn processes for flock sessions and residents)
```

The sandbox starts with the OpenShell supervisor. Völundr then runs the Skuld
session command via gateway `ExecSandbox` and exposes Skuld via gateway
`ExposeService`.

## Authentication Boundaries

The runtime uses three separate authentication paths:

| Caller | Target | Authentication |
| --- | --- | --- |
| Völundr | OpenShell gateway | Keycloak client-credentials bearer token |
| Sandbox supervisor | OpenShell gateway | projected service-account bootstrap and gateway sandbox JWT |
| Sandbox provider proxy | Völundr credential endpoint | SPIFFE JWT-SVID from the CSI Workload API socket |

The Keycloak machine client is not an operator account. Configure a confidential
service-account client with audience `openshell`, assign the required OpenShell
role, and expose its secret to Völundr as `openshell-volundr-agent-oidc`.

## Kubernetes Prerequisites

Each cluster that can run OpenShell sessions needs:

1. SPIRE server, node agents, controller manager, and SPIFFE CSI driver.
2. A unique SPIFFE trust domain backed by that cluster's SPIRE authority.
3. A network-routable OIDC discovery endpoint with valid TLS and DNS.
4. A `ClusterSPIFFEID` selecting OpenShell-managed sandbox pods.
5. OpenShell gateway setting `server.providerTokenGrants.spiffe.enabled=true`.
6. The SPIFFE Workload API socket mounted at
   `/spiffe-workload-api/spire-agent.sock`.

The Niuu deployment uses this identity matrix:

| Cluster | Trust domain | JWT issuer |
| --- | --- | --- |
| `eitri` | `eitri.niuu.world` | `https://spire-oidc.eitri.asgard.niuu.world` |
| `glitnir` | `glitnir.niuu.world` | `https://spire-oidc.glitnir.asgard.niuu.world` |
| `jarnvidr` | `jarnvidr.niuu.world` | `https://spire-oidc.jarnvidr.asgard.niuu.world` |
| `noatun` | `niuu.world` | `https://spire-oidc.noatun.asgard.niuu.world` |
| `valaskjalf` | `valaskjalf.niuu.world` | `https://spire-oidc.valaskjalf.asgard.niuu.world` |
| `valhalla` | `valhalla.niuu.world` | `https://spire-oidc.valhalla.asgard.niuu.world` |
| `vanaheim` | `vanaheim.niuu.world` | `https://spire-oidc.vanaheim.asgard.niuu.world` |
| `ymir` | `ymir.niuu.world` | `https://spire-oidc.ymir.asgard.niuu.world` |

Independent SPIRE servers must not issue the same trust domain unless they share
an upstream authority or are explicitly federated. Noatun keeps its established
`niuu.world` domain; newer clusters use cluster-qualified domains.

The registration entry selects namespace `openshell-sandboxes`, pod label
`openshell.ai/managed-by=openshell`, and pod annotation
`openshell.io/sandbox-id`. It issues:

```text
spiffe://TRUST_DOMAIN/openshell/sandbox/SANDBOX_ID
```

The corresponding hardened SPIRE chart values are:

```yaml
spire-server:
  controllerManager:
    identities:
      clusterSPIFFEIDs:
        openshell-sandboxes:
          enabled: true
          spiffeIDTemplate: 'spiffe://{{ .TrustDomain }}/openshell/sandbox/{{ index .PodMeta.Annotations "openshell.io/sandbox-id" }}'
          namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: openshell-sandboxes
          podSelector:
            matchLabels:
              openshell.ai/managed-by: openshell
          jwtTTL: 5m
```

The OpenShell gateway enables the Workload API mount independently of its OIDC
caller authentication:

```yaml
server:
  oidc:
    issuer: https://keycloak.niuu.world/realms/volundr
    audience: openshell
    rolesClaim: realm_access.roles
    adminRole: openshell-admin
    userRole: openshell-user
  providerTokenGrants:
    spiffe:
      enabled: true
      workloadApiSocketPath: /spiffe-workload-api/spire-agent.sock
```

The infrastructure repository owns the concrete GitOps deployment in
`spire/`, with operator procedures in `docs/runbooks/spire.md` and
`docs/runbooks/openshell.md`. The `local` cluster must not include `/spire`.

OpenShell `0.0.78` requires the supervisor startup fix from
[NVIDIA/OpenShell PR 2012](https://github.com/NVIDIA/OpenShell/pull/2012) when
SPIFFE provider grants are enabled. The infrastructure runbook pins a
version-matched public supervisor image carrying that fix; remove the pin after
upgrading to an upstream release that includes it.

## Configure The Pod Manager

```yaml
pod_manager:
  adapter: "volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager"
  kwargs:
    gateway_endpoint: "openshell.openshell.svc.cluster.local:8080"
    token_url: "https://keycloak.niuu.world/realms/volundr/protocol/openid-connect/token"
    client_id: "openshell-volundr-agent"
    sandbox_image: "ghcr.io/niuulabs/skuld:openshell-codex-openbao-20260709-7"
    sandbox_command: ["/usr/local/bin/openshell-run-installed-skuld"]
    sandbox_workspace: "/sandbox/workspace"
    sandbox_home: "/sandbox"
    credential_token_endpoint: "http://niuu-volundr.volundr.svc.cluster.local/api/v1/internal/openshell/credential-token"
    spiffe_jwks_uri: "https://spire-oidc.noatun.asgard.niuu.world/keys"
    spiffe_issuer: "https://spire-oidc.noatun.asgard.niuu.world"
    spiffe_audience: "http://niuu-volundr.volundr.svc.cluster.local/api/v1/internal/openshell/credential-token"
    spiffe_subject_prefix: "spiffe://niuu.world/openshell/sandbox/"
    codex_oauth_token_url: "https://auth.openai.com/oauth/token"
    codex_oauth_client_id: "app_EMoamEEZ73f0CkXaXp7hrann"
    codex_refresh_skew_seconds: 300
    service_port: 9200
  secret_kwargs_env:
    client_secret: OPENSHELL_OIDC_CLIENT_SECRET
```

In Helm values, mount the Keycloak client secret with `podManager.secretKwargs`:

```yaml
podManager:
  adapter: "volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager"
  kwargs:
    gateway_endpoint: "openshell.openshell.svc.cluster.local:8080"
    token_url: "https://keycloak.niuu.world/realms/volundr/protocol/openid-connect/token"
    client_id: "openshell-volundr-agent"
  secretKwargs:
    - kwarg: "client_secret"
      secretName: "openshell-volundr-agent-oidc"
      secretKey: "client-secret"
```

All gateway and OIDC settings enter through adapter kwargs and secret kwargs.
There are no implicit process-environment fallbacks.

## Configure Resident Ravns

The same `OpenShellGatewayPodManager` implements the resident runtime controller
port. When it is the configured pod manager, Völundr registers it automatically;
do not configure a second OpenShell controller or credential-grant broker.

```yaml
residentRuntimeProfiles:
  - id: ravn-openshell
    enabled: true
    displayName: Resident Ravn (OpenShell)
    backend: openshell
    engine: ravn
    capabilities: [chat, runtime.restart, logs, usage]
    defaultModel: gpt-5.6-sol
    allowedModels: [gpt-5.6-sol]
    deployment:
      values:
        image:
          repository: ghcr.io/niuulabs/openshell
          tag: niu-1099-openshell-resident
        broker:
          cliType: codex-ws
          transportAdapter: skuld.transports.codex_ws.CodexWebSocketTransport
          skipPermissions: true
        session:
          reasoningEffort: high
        openshell:
          codexAuth:
            credentialName: codex-credentials
            authField: auth.json
        resident:
          platform:
            enabled: true
            baseUrl: https://yggdrasil.niuu.world
        mimir:
          instances:
            - name: mimir-yggdrasil
              role: shared
              url: https://mimir.yggdrasil.niuu.world/api/v1
              auth:
                type: workload
                audiences: [mimir]
```

The adapter translates the existing resident `deployment.values` contract into
one sandbox, generated Skuld/Ravn configuration, dynamic Provider v2 grants,
and detached supervised processes. Restart reconstructs the complete process
plan from the same profile while retaining the sandbox workspace. Delete removes
the exposed service, sandbox, provider instances, and provider profiles.

OpenShell does not currently implement resident suspend/resume or a native usage
API, so those capabilities must not be advertised. `usage` is the existing Skuld
model-usage report sent with the resident-bound platform token. `logs` uses the
gateway's bounded `GetSandboxLogs` API.

## Supported Session Inputs

The OpenShell gateway API supports a sandbox template, not an arbitrary
multi-container Kubernetes pod. The adapter maps the supported Volundr session
surface:

| Volundr input | OpenShell mapping |
| --- | --- |
| Session labels and annotations | Sandbox template labels and annotations |
| Literal env values | Sandbox environment |
| `resources.requests` / `resources.limits` | Sandbox template resources |
| `nodeSelector` | Kubernetes driver `pod.node_selector` |
| `tolerations` | Kubernetes driver `pod.tolerations` |
| `runtimeClassName` | Kubernetes driver `pod.runtime_class_name` |
| `priorityClassName` | Kubernetes driver `pod.priority_class_name` |

Ravn flock contributions are translated into a structured OpenShell process plan.
The sandbox contains one Skuld process and one Ravn daemon process per persona,
with shared workspace and mesh addresses. Other arbitrary extra containers, init
containers, volume mounts, and service accounts remain unsupported by the
OpenShell Kubernetes driver and are logged.

## Credentials And Agent Home

OpenBao remains the source of truth. API credentials use OpenShell Provider v2
dynamic token grants:

1. Völundr creates an empty provider instance and profile for the session mapping.
2. The sandbox supervisor obtains a SPIFFE JWT-SVID from SPIRE.
3. The supervisor exchanges the assertion at Völundr's internal OAuth endpoint.
4. Völundr verifies the SVID, attached provider, sandbox label, session, owner, and
   requested OpenBao field before returning the credential.
5. OpenShell caches the short-lived response and injects it only for the profile's
   matching HTTP endpoints.

No API credential value is persisted in OpenShell or placed in the sandbox process
environment by Völundr.

Provider v2 handles credentials and network policy, not arbitrary home-directory
mounts. Stable, non-secret defaults such as Codex `config.toml` belong in the
sandbox image or session configuration.

Codex subscription authentication uses an OpenBao-backed dynamic grant:

```yaml
openshell:
  codexAuth:
    credentialName: codex-credentials
    authField: auth.json
```

The sandbox image generates `~/.codex/auth.json` locally. It contains only the
OpenShell runtime reference `openshell:resolve:env:CODEX_AUTH_ACCESS_TOKEN`, the
non-secret account ID, and a metadata-only ID token. OpenShell replaces the
outbound Authorization header with the SPIFFE-brokered access token. Völundr
refreshes expiring Codex OAuth tokens and persists rotations back to the same
OpenBao credential.

Claude Code's built-in profile supports API keys. Claude subscription OAuth state
is not a first-class OpenShell provider credential, so it is not represented as a
supported dynamic grant. Do not upload agent authentication files or mount the
legacy Völundr home PVC into OpenShell sandboxes.

## Operational Checks

Check that the OpenShell gateway has OIDC enabled and can validate Keycloak
tokens:

```bash
kubectl -n openshell logs statefulset/openshell
```

Expected gateway startup logs include OIDC discovery/JWKS loading and JWT
validation enabled. Once Völundr starts a session, the corresponding OpenShell
Sandbox should reach Ready and report a connected supervisor.

Validate SPIRE and its issuer with the cluster kubeconfig:

```bash
cluster=noatun
export KUBECONFIG="$HOME/.kube/kubeconfigs/$cluster.yaml"

kubectl -n spire get pods
kubectl -n spire get configmap spire-bundle
kubectl get clusterspiffeid spire-spire-openshell-sandboxes
kubectl -n spire wait --for=condition=Ready \
  certificate/spire-oidc-tls --timeout=5m
curl --fail --silent \
  "https://spire-oidc.$cluster.asgard.niuu.world/.well-known/openid-configuration" \
  | jq
```

For a live session, verify that the sandbox pod has the OpenShell label, sandbox
ID annotation, and SPIFFE CSI socket. The issued JWT-SVID subject must start with
the configured `spiffe_subject_prefix`, its audience must equal
`spiffe_audience`, and its issuer must equal `spiffe_issuer`.

The Niuu OpenShell sandbox image is public and does not require a GHCR pull
secret. The gateway exposes Prometheus metrics on port `9090`; Kubernetes
container logs remain the source for gateway and supervisor logs.

Stopping a session explicitly deletes its exposed service, sandbox, provider
instances, and provider profiles. Launch rollback performs the same cleanup.

Upstream references:

- https://docs.nvidia.com/openshell/latest/kubernetes/setup
- https://docs.nvidia.com/openshell/kubernetes/access-control
- https://docs.nvidia.com/openshell/sandboxes/providers-v2
- https://spiffe.io/docs/latest/deploying/configuring/
