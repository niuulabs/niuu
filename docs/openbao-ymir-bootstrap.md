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
  url: "https://openbao.ymir.niuu.world"
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
    url: "https://openbao.ymir.niuu.world"
    mount_path: "volundr"
    auth_method: "token"
  secretKwargs:
    - kwarg: token
      secretName: openbao-app-auth
      secretKey: token
```

For Ting, keep the same adapter but set `mount_path: "ting"`.

For runtime pod injection, use the dynamic OpenBao injector adapter:

```yaml
secretInjection:
  adapter: "volundr.adapters.outbound.openbao_secret_injection.OpenBaoAgentInjectionAdapter"
  kwargs:
    openbao_url: "https://openbao.ymir.niuu.world"
    namespace: "skuld"
    mount_path: "volundr"
    auth_path: "jwt-valhalla"
    audience: "https://kubernetes.default.svc.cluster.local"
  secretKwargs:
    - kwarg: token
      secretName: openbao-app-auth
      secretKey: token
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
  both `token` and `approle` auth.
