# Secret Management

Volundr uses CSI-based secret injection. The API server never sees secret values — it generates pod spec additions that tell the CSI driver what to mount.

## Adapters

| Adapter | Config key | Description |
|---------|-----------|-------------|
| `InMemorySecretInjectionAdapter` | default | No-op for development |
| `InfisicalCSISecretInjectionAdapter` | `secret_injection` | Infisical Secrets Operator + CSI |

### Infisical

```yaml
secret_injection:
  adapter: "volundr.adapters.outbound.infisical_secret_injection.InfisicalCSISecretInjectionAdapter"
  kwargs:
    infisical_url: "https://infisical.example.com"
    client_id: "machine-identity-id"
    client_secret: "machine-identity-secret"
    namespace: "volundr-sessions"
```

The adapter creates `InfisicalSecret` CRDs and returns `PodSpecAdditions` with CSI volume definitions. The Infisical Secrets Operator handles the actual secret fetch and CSI mount.

## Secret repository (OpenBao / Vault)

For direct secret storage (not CSI-based):

| Adapter | Description |
|---------|-------------|
| `InMemorySecretRepository` | Development only |
| `OpenBaoSecretRepository` | OpenBao/Vault KV v2 |

The `SecretRepository` port handles:

- Storing and retrieving credentials at paths
- Provisioning per-user policies and K8s auth roles
- Creating ephemeral session secrets with Vault Agent config
- Cleanup on user deprovisioning

```yaml
# OpenBao example (via secret_injection or direct)
secret_injection:
  adapter: "volundr.adapters.outbound.openbao.OpenBaoSecretInjectionAdapter"
  kwargs:
    url: "http://openbao:8200"
    auth_method: "kubernetes"
    mount_path: "secret"
```
