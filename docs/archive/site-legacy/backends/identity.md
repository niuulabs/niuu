# Identity

Identity is handled by the `IdentityPort`, which validates JWTs and provisions users on first login (JIT).

## Adapters

| Adapter | Description |
|---------|-------------|
| `AllowAllIdentityAdapter` | No auth, returns a default principal (development) |
| `EnvoyHeaderIdentityAdapter` | Extracts identity from Envoy auth headers (production) |

### Allow all (default)

```yaml
identity:
  adapter: "volundr.adapters.outbound.identity.AllowAllIdentityAdapter"
```

### Envoy headers

In production, Envoy sits in front of Volundr and handles OIDC flows. It sets headers with the authenticated user's claims:

```yaml
identity:
  adapter: "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"
  kwargs:
    user_id_header: "x-auth-user-id"
    email_header: "x-auth-email"
```

## Role mapping

IDP roles are mapped to Volundr roles via config:

```yaml
identity:
  role_mapping:
    admin: "volundr:admin"
    developer: "volundr:developer"
    viewer: "volundr:viewer"
```

## JIT provisioning

On first login, the identity adapter calls `get_or_provision_user`, which:

1. Checks if the user exists in the database
2. If not, creates the user record
3. Provisions storage (home PVC) via the storage adapter
4. Returns the user

This is IDP-agnostic — the code does not depend on any specific identity provider (Keycloak, Entra ID, Okta, etc.).
