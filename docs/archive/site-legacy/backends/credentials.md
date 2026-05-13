# Credential Stores

Credentials (API keys, OAuth tokens, SSH keys, etc.) are stored in a pluggable backend. The API exposes metadata only — values are never returned.

## Adapters

| Adapter | Config key | Description |
|---------|-----------|-------------|
| `MemoryCredentialStore` | default | In-memory, development only |
| `VaultCredentialStore` | `credential_store` | HashiCorp Vault / OpenBao |
| `InfisicalCredentialStore` | `credential_store` | Infisical |

### Vault

```yaml
credential_store:
  adapter: "volundr.adapters.outbound.vault_credential_store.VaultCredentialStore"
  kwargs:
    url: "http://vault:8200"
    auth_method: "kubernetes"
    mount_path: "secret"
```

### Infisical

```yaml
credential_store:
  adapter: "volundr.adapters.outbound.infisical_credential_store.InfisicalCredentialStore"
  kwargs:
    url: "https://infisical.example.com"
    client_id: "..."
    client_secret: "..."
```

## Credential types

| Type | Description |
|------|-------------|
| `api_key` | Single API key |
| `oauth_token` | OAuth access and refresh tokens |
| `git_credential` | Git username/password or PAT |
| `ssh_key` | SSH key pair |
| `tls_cert` | TLS certificate and private key |
| `generic` | Arbitrary key-value pairs |

## Ownership

Credentials are scoped by owner:

- **User credentials** — owned by a specific user
- **Tenant credentials** — shared across a tenant (admin only)

The `CredentialService` handles mount strategy selection based on credential type, generating appropriate `SecretMountSpec` entries for pod injection.
