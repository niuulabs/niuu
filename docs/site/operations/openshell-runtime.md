# OpenShell Runtime

OpenShell mode creates Forge sessions through the OpenShell gateway API. Völundr
does not shell out to the OpenShell CLI; it mints a Keycloak client-credentials
token and calls the gateway gRPC service directly.

## Runtime Shape

```text
Völundr
  -> OpenShellGatewayPodManager
  -> OpenShell gateway gRPC API
  -> Kubernetes compute driver
  -> OpenShell Sandbox
  -> Skuld broker (+ Ravn processes for flock sessions)
```

The sandbox starts with the OpenShell supervisor. Völundr then runs the Skuld
session command via gateway `ExecSandbox` and exposes Skuld via gateway
`ExposeService`.

## Configure The Pod Manager

```yaml
pod_manager:
  adapter: "volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager"
  kwargs:
    gateway_endpoint: "openshell.openshell.svc.cluster.local:8080"
    token_url: "https://keycloak.niuu.world/realms/volundr/protocol/openid-connect/token"
    client_id: "openshell-volundr-agent"
    sandbox_image: "ghcr.io/niuulabs/niuu-openshell:openshell-provider-v2-20260709"
    sandbox_command: ["/opt/niuu/bin/python", "-m", "skuld"]
    sandbox_home: "/sandbox"
    credential_token_endpoint: "http://niuu-volundr.volundr.svc.cluster.local/api/v1/internal/openshell/credential-token"
    spiffe_jwks_uri: "https://spire-spiffe-oidc-discovery-provider.spire.svc.cluster.local/keys"
    spiffe_issuer: "https://spire-spiffe-oidc-discovery-provider.spire.svc.cluster.local"
    spiffe_audience: "http://niuu-volundr.volundr.svc.cluster.local/api/v1/internal/openshell/credential-token"
    spiffe_subject_prefix: "spiffe://niuu.world/openshell/sandbox/"
    spiffe_ca_cert_path: "/etc/spire/ca.crt"
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

The adapter also honors these environment variables:

- `OPENSHELL_GATEWAY_ENDPOINT`
- `OPENSHELL_GATEWAY_PUBLIC_URL`
- `OPENSHELL_OIDC_TOKEN_URL`
- `OPENSHELL_OIDC_CLIENT_ID`
- `OPENSHELL_OIDC_CLIENT_SECRET`

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

OAuth client state and agent configuration are files, which Provider v2 does not
materialize. Map those OpenBao fields explicitly; Völundr projects them with mode
`0600` under the OpenShell user's real home (`/sandbox`):

```yaml
openshell:
  credentialMappings:
    - credentialName: claude-credentials
      fileMappings:
        /home/volundr/.claude/.credentials.json: credentials.json
        /home/volundr/.claude/settings.json: settings.json
    - credentialName: codex-credentials
      fileMappings:
        /home/volundr/.codex/auth.json: auth.json
        /home/volundr/.codex/config.toml: config.toml
```

This is the Kubernetes-compatible path for Claude subscription authentication
described in NVIDIA/OpenShell issue 620. The same mapping carries Codex OAuth and
configuration without requiring interactive login in every sandbox.

## Operational Checks

Check that the OpenShell gateway has OIDC enabled and can validate Keycloak
tokens:

```bash
kubectl -n openshell logs statefulset/openshell
```

Expected gateway startup logs include OIDC discovery/JWKS loading and JWT
validation enabled. Once Völundr starts a session, the corresponding OpenShell
Sandbox should reach Ready and report a connected supervisor.

Stopping a session explicitly deletes its exposed service, sandbox, provider
instances, and provider profiles. Launch rollback performs the same cleanup.
