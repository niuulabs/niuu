# OpenBao on Ymir

This is the intended self-hosted shape for replacing Infisical:

- `ymir` hosts the primary OpenBao cluster
- OpenBao uses `seal "static"` for auto-unseal
- the static seal key comes from Doppler via External Secrets
- workload clusters authenticate to Ymir OpenBao via JWT auth
- app credentials live in app-specific KV v2 mounts:
  - `volundr/data/users/{user_id}/{credential_name}`
  - `ting/data/users/{user_id}/{credential_name}`

## Static Seal

Generate one 32-byte key and store it in Doppler as
`YMIR_OPENBAO_STATIC_UNSEAL_KEY`.

Sync it into the cluster with External Secrets:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: openbao-seal
  namespace: openbao
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: doppler-store
    kind: ClusterSecretStore
  target:
    name: openbao-seal
    creationPolicy: Owner
  data:
    - secretKey: current.key
      remoteRef:
        key: YMIR_OPENBAO_STATIC_UNSEAL_KEY
```

Mount the secret into the OpenBao pod and reference it from the config:

```hcl
seal "static" {
  current_key_id = "20260518-1"
  current_key    = "file:///openbao/seal/current.key"
}
```

For rotations, add `previous_key_id` and `previous_key`.

## Bootstrap Job

Use [scripts/openbao/bootstrap.py](/Users/jozefvaneenbergen/git/niuu/software/volundr/scripts/openbao/bootstrap.py)
from a lightweight Kubernetes `Job` or CI runner.

Example bootstrap spec:

```yaml
openbao:
  url: "https://openbao.niuu.world"
  namespace: ""
  auth:
    method: token
    token_env: OPENBAO_TOKEN

mounts:
  - path: volundr
    description: "Volundr app credentials"
  - path: ting
    description: "Ting app credentials"

jwtAuthBackends:
  - path: jwt-valhalla
    description: "Valhalla workload JWT auth"
    oidc_discovery_url: "https://kubernetes.default.svc.cluster.local/.well-known/openid-configuration"
    bound_issuer: "https://kubernetes.default.svc.cluster.local"

policies:
  - name: volundr-default
    policy: |
      path "volundr/data/users/*" {
        capabilities = ["deny"]
      }
  - name: ting-default
    policy: |
      path "ting/data/users/*" {
        capabilities = ["deny"]
      }
```

Run it:

```bash
export OPENBAO_TOKEN="..."
uv run python scripts/openbao/bootstrap.py path/to/bootstrap.yaml
```

`serviceAccountAccess` is intentionally omitted from the static example. In the
recommended model, user-or-session scoped access is provisioned dynamically by
the application at runtime rather than maintained as a giant static YAML list.

## Adapter Configuration

Use the shared OpenBao credential store:

```yaml
credentialStore:
  adapter: "niuu.adapters.openbao_credential_store.OpenBaoCredentialStore"
  kwargs:
    url: "https://openbao.niuu.world"
    mount_path: "volundr"
    auth_method: "jwt"
    jwt_mount_path: "auth/jwt-ymir"
    jwt_role: "volundr-app"
    jwt_token_file: "/var/run/secrets/kubernetes.io/serviceaccount/token"
  secretKwargs: []
```

For shared user credentials, Volundr, Niuu shared services, and Ting must use
the same URL, namespace, and KV mount. The umbrella chart overlay
[`charts/niuu/values-openbao.yaml`](../charts/niuu/values-openbao.yaml) configures
all three with the Ymir URL and `volundr` mount above. Apply it after your base
values. Each service authenticates with its projected Kubernetes ServiceAccount
JWT and its own role (`volundr-app`, `niuu-shared-app`, or `ting-app`). For other
clusters, set each service's `jwt_mount_path` to that cluster's auth backend.
Provision the roles bound to their ServiceAccounts before deploying the values,
with an OpenBao policy allowing reads,
writes, and deletes under `volundr/data/users/*` and `volundr/data/tenants/*`,
plus listing under the matching `volundr/metadata/` paths.

Use a separate `ting` mount only for intentionally isolated Ting credentials;
credentials created in Volundr settings will not appear there. Existing
credentials in a different mount are not migrated by changing configuration.

Development defaults use one worker and one replica with the in-memory store;
credentials are lost on restart. Configure OpenBao before scaling workers or
replicas. OpenBao read/list failures surface as errors rather than successful
empty or partial credential lists; a missing path (HTTP 404) remains an expected
empty result.

For runtime pod injection, use the dynamic OpenBao injector adapter:

```yaml
secretInjection:
  adapter: "volundr.adapters.outbound.openbao_secret_injection.OpenBaoAgentInjectionAdapter"
  kwargs:
    openbao_url: "https://openbao.niuu.world"
    namespace: "skuld"
    mount_path: "volundr"
    auth_path: "jwt-valhalla"
    audience: "https://kubernetes.default.svc.cluster.local"
    auth_method: "jwt"
    jwt_mount_path: "auth/jwt-valhalla"
    jwt_role: "volundr-app"
    jwt_token_file: "/var/run/secrets/kubernetes.io/serviceaccount/token"
  secretKwargs: []
```

This adapter creates the session ServiceAccount, JWT role, and agent ConfigMap
at session startup instead of relying on static `serviceAccountAccess` YAML.

## Path Layout

Recommended KV v2 layout:

- `volundr/data/users/{user_id}/{credential_name}`
- `volundr/data/tenants/{tenant_id}/shared/{credential_name}`
- `ting/data/users/{user_id}/{credential_name}`

The adapter also uses matching metadata paths for lists:

- `volundr/metadata/users/{user_id}`
- `ting/metadata/users/{user_id}`

## Notes

- OpenBao becomes a control-plane dependency, similar to Keycloak.
- JWT auth avoids Kubernetes `TokenReview` and works well with a central OpenBao.
- Workload clusters need the OpenBao injector installed in external mode and
  pointed at the Ymir OpenBao address.
- `niuu.adapters.openbao_credential_store.OpenBaoCredentialStore` supports
  `jwt`, `token`, and `approle` auth. Kubernetes workloads use `jwt`; no static
  application token is needed.
