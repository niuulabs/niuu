# Identity & Authorization

Two ports, typically configured together. Identity determines who the user is. Authorization determines what they can do.

## Identity (IdentityPort)

| Adapter | Use case |
|---------|----------|
| `AllowAllIdentityAdapter` | Development — no auth, returns a default principal |
| `EnvoyHeaderIdentityAdapter` | Production — trusts Envoy sidecar headers |

### AllowAll (default)

```yaml
identity:
  adapter: "volundr.adapters.outbound.identity.AllowAllIdentityAdapter"
```

No configuration needed. Every request gets a default principal. Use this for local development only.

### Envoy Headers (production)

```yaml
identity:
  adapter: "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"
  kwargs:
    user_id_header: "x-auth-user-id"
    email_header: "x-auth-email"
    tenant_header: "x-auth-tenant"
    roles_header: "x-auth-roles"
```

Requires the Envoy sidecar enabled in Helm:

```yaml
envoy:
  enabled: true
  jwt:
    enabled: true
    issuer: "https://keycloak.example.com/realms/volundr"
    audiences: [volundr]
    jwksUri: "https://keycloak.example.com/realms/volundr/protocol/openid-connect/certs"
```

### JIT Provisioning

On first login, the identity adapter creates the user record, provisions home PVC storage, and assigns tenant membership from IDP claims. This is IDP-agnostic. It works with Keycloak, Entra ID, Okta, or any OIDC provider.

### Role Mapping

Maps IDP claim roles to Volundr roles:

```yaml
identity:
  roleMapping:
    admin: "volundr:admin"
    developer: "volundr:developer"
    viewer: "volundr:viewer"
```

## Authorization (AuthorizationPort)

| Adapter | Description |
|---------|-------------|
| `AllowAllAuthorizationAdapter` | Development — permits everything |
| `SimpleRoleAuthorizationAdapter` | Role-based with ownership checks |
| `CerbosAuthorizationAdapter` | Full policy engine via Cerbos PDP |

### AllowAll (default)

Every action is permitted. Development only.

### SimpleRole (recommended minimum)

```yaml
authorization:
  adapter: "volundr.adapters.outbound.authorization.SimpleRoleAuthorizationAdapter"
```

Enforces these rules:

- Cross-tenant access is denied.
- Admins get full access within their tenant.
- Developers can read/write their own resources.
- Viewers get read-only access.

### Cerbos (full policy engine)

```yaml
authorization:
  adapter: "volundr.adapters.outbound.cerbos.CerbosAuthorizationAdapter"
  kwargs:
    url: "http://cerbos:3592"
    timeout: 5
```

Delegates all authorization decisions to a Cerbos PDP instance. Use this when you need fine-grained, policy-as-code authorization.
