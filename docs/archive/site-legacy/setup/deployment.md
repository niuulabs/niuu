# Deployment Prerequisites and Infrastructure Requirements

This guide documents every infrastructure dependency for deploying Ting, Volundr, and the
niuu umbrella chart. Follow the prerequisites checklist before your first `helm install`
to avoid the most common failure modes.

---

## Prerequisites Checklist

### Shared Infrastructure

- [ ] **PostgreSQL cluster** accessible from the deployment namespace (two databases: `volundr`, `ting`)
- [ ] **Keycloak 26+** (or compatible OIDC provider) with `token-exchange` feature enabled
- [ ] **Ingress controller** installed (NGINX, Traefik, or HAProxy) with a single domain for all services
- [ ] **TLS certificate** for the shared domain (wildcard or SAN covering the base domain)
- [ ] **Credential store** configured — Infisical or Vault — if tracker integrations are needed

### Kubernetes Secrets

| Secret Name | Keys | Used By | Purpose |
|---|---|---|---|
| `volundr-db` | `username`, `password` | Volundr | PostgreSQL credentials |
| `ting-db` | `username`, `password` | Ting | PostgreSQL credentials |
| `volundr-pat-issuer` | `client-secret` | Volundr, Ting | Keycloak PAT issuer client secret |
| `infisical-auth` | `client-id`, `client-secret` | Volundr, Ting | Infisical Universal Auth (if using Infisical) |
| `github-token` | `token` | Volundr | GitHub API token for repo access |

> Create secrets before deploying. Use ExternalSecrets or Sealed Secrets for production —
> see `keycloak-pat-issuer.md` for the PAT issuer secret workflow.

### Keycloak Setup

Refer to [keycloak-pat-issuer.md](keycloak-pat-issuer.md) for the full walkthrough. Summary:

1. **Enable `token-exchange`** and `admin-fine-grained-authz` features in the Keycloak CR.
2. **Create the `volundr-pat-issuer` client** — confidential, service-account-enabled, with `manage-users` role.
3. **Add audience mappers** to `volundr-web` and `volundr-cli` clients so their tokens include `volundr-pat-issuer` in the `aud` claim.
4. **Add audience mapper** to `volundr-pat-issuer` so exchanged tokens include `volundr-api` in `aud`.
5. **Set token lifespan** to 365 days on the `volundr-pat-issuer` client (Access Token Lifespan, Client Session Max, Client Session Idle).
6. **Store the client secret** in a K8s secret named `volundr-pat-issuer`.

---

## Domain Routing

All three ingresses — Volundr API, Ting API, and Volundr Web — **must share the same host**.
The ingress controller routes by path prefix:

| Path | Service | Chart |
|---|---|---|
| `/api/v1/*` | Volundr API | `charts/volundr` |
| `/api/v1/users/*` | Volundr API | `charts/volundr` |
| `/api/v1/niuu/*` | Volundr API | `charts/volundr` |
| `/api/v1/ting/*` | Ting API | `charts/ting` |
| `/` (catch-all) | Volundr Web UI | `charts/volundr` (web subchart) |

### Example Ingress Values

```yaml
# niuu umbrella chart values
global:
  domain: niuu.example.com

volundr:
  ingress:
    enabled: true
    className: nginx
    hosts:
      - host: niuu.example.com
        paths:
          - path: /api/v1
            pathType: Prefix
          - path: /api/v1/users
            pathType: Prefix
          - path: /api/v1/niuu
            pathType: Prefix
    tls:
      - secretName: niuu-tls
        hosts:
          - niuu.example.com

  web:
    enabled: true
    ingress:
      enabled: true
      className: nginx
      hosts:
        - host: niuu.example.com
          paths:
            - path: /
              pathType: Prefix
      tls:
        - secretName: niuu-tls
          hosts:
            - niuu.example.com

ting:
  ingress:
    enabled: true
    className: nginx
    hosts:
      - host: niuu.example.com
        paths:
          - path: /api/v1/ting
            pathType: Prefix
    tls:
      - secretName: niuu-tls
        hosts:
          - niuu.example.com
```

---

## Helm Values Walkthrough

### Niuu Umbrella Chart (`charts/niuu`)

The umbrella chart propagates global values to all subcharts:

```yaml
global:
  imagePullSecrets: []          # shared pull secrets
  image:
    registry: ghcr.io/niuulabs  # shared image registry
  domain: niuu.example.com       # base domain for ingress hostnames
  aiModels:                      # propagated to both Ting and Volundr
    - id: "claude-opus-4-6"
      name: "Opus 4.6"
      costPerMillionTokens: 15.00
    - id: "claude-sonnet-4-6"
      name: "Sonnet 4.6"
      costPerMillionTokens: 3.00
    - id: "claude-haiku-4-5-20251001"
      name: "Haiku 4.5"
      costPerMillionTokens: 1.00

volundr:
  enabled: true   # deploy Volundr subchart

ting:
  enabled: true   # deploy Ting subchart
```

### Volundr Subchart (`charts/volundr`)

#### Database

```yaml
database:
  name: volundr
  existingSecret: "volundr-db"   # K8s secret with username/password keys
  userKey: username
  passwordKey: password
  minPoolSize: 5
  maxPoolSize: 20
  external:
    enabled: true
    host: postgresql.default.svc.cluster.local
    port: 5432
```

#### Envoy Sidecar (Required for Authenticated Deployments)

Without Envoy, Bearer tokens from the web UI are not validated and all API calls
return 401 or fall through to HTML responses.

```yaml
envoy:
  enabled: true                  # MUST be true for any non-dev deployment
  jwt:
    enabled: true
    issuer: "https://keycloak.example.com/realms/volundr"
    audiences:
      - volundr-api
    jwksUri: "https://keycloak.example.com/realms/volundr/protocol/openid-connect/certs"
    keycloakHost: "keycloak.default.svc.cluster.local"
    keycloakPort: 8080
    rolesClaim: "resource_access.volundr.roles"
    tenantClaim: "tenant_id"
    bypassPrefixes:
      - /api/v1/auth/config
      - /health
```

The Envoy sidecar extracts JWT claims into headers forwarded to the application:

| Claim | Header |
|---|---|
| `sub` | `x-auth-user-id` |
| `email` | `x-auth-email` |
| `tenant_id` | `x-auth-tenant` |
| `resource_access.volundr.roles` | `x-auth-roles` |

#### Identity Adapter (Production)

Switch from `AllowAllIdentityAdapter` to Envoy trusted headers:

```yaml
identity:
  adapter: "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"
  kwargs:
    user_id_header: "x-auth-user-id"
    email_header: "x-auth-email"
    tenant_header: "x-auth-tenant"
    roles_header: "x-auth-roles"
```

#### Credential Store (Required for Integrations)

The default `MemoryCredentialStore` stores nothing at runtime — all credential
lookups return empty. Linear, GitHub, and other tracker adapters will silently fail.

```yaml
# Infisical example
credentialStore:
  adapter: "niuu.adapters.infisical_credential_store.InfisicalCredentialStore"
  kwargs:
    site_url: "https://app.infisical.com"
    client_id: "<infisical-client-id>"
    project_id: "<infisical-project-id>"
  secretKwargs:
    - kwarg: client_secret
      secretName: infisical-auth
      secretKey: client-secret
```

#### Web UI

```yaml
web:
  enabled: true
  config:
    oidc:
      authority: "https://keycloak.example.com/realms/volundr"
      clientId: "volundr-web"
      scope: "openid profile email"
```

#### Storage

Volundr requires `ReadWriteMany` PVCs for session and home directories:

```yaml
storage:
  sessions:
    enabled: true
    storageClass: longhorn      # must support RWX
    accessMode: ReadWriteMany
    size: 1Gi
  home:
    enabled: true
    storageClass: longhorn
    accessMode: ReadWriteMany
    size: 1Gi
```

### Ting Subchart (`charts/ting`)

#### Database

Ting uses a **separate database** from Volundr on the same PostgreSQL cluster:

```yaml
database:
  name: ting
  existingSecret: "ting-db"
  userKey: username
  passwordKey: password
  minPoolSize: 2
  maxPoolSize: 10
  external:
    enabled: true
    host: postgresql.default.svc.cluster.local
    port: 5432
```

#### Envoy Sidecar

Same configuration pattern as Volundr but with Ting-specific roles claim.

> **Why `audiences: [volundr-api]` for both Volundr and Ting?** Both services
> share the same Keycloak realm and accept the same user JWTs. The `volundr-api`
> audience is configured once in the IDP and used by all backend services. Ting
> differentiates authorization via the `rolesClaim` (which reads from
> `resource_access.ting.roles` instead of `resource_access.volundr.roles`), not
> via a separate audience.

```yaml
envoy:
  enabled: true
  jwt:
    enabled: true
    issuer: "https://keycloak.example.com/realms/volundr"
    audiences:
      - volundr-api              # shared audience — see note above
    jwksUri: "https://keycloak.example.com/realms/volundr/protocol/openid-connect/certs"
    keycloakHost: "keycloak.default.svc.cluster.local"
    keycloakPort: 8080
    rolesClaim: "resource_access.ting.roles"
    bypassPrefixes:
      - /health
```

#### Credential Store

Must match Volundr's credential store — both services read from the same backend:

```yaml
credentialStore:
  adapter: "niuu.adapters.infisical_credential_store.InfisicalCredentialStore"
  kwargs:
    site_url: "https://app.infisical.com"
    client_id: "<infisical-client-id>"
    project_id: "<infisical-project-id>"
  secretKwargs:
    - kwarg: client_secret
      secretName: infisical-auth
      secretKey: client-secret
```

#### Volundr Connection

Ting calls Volundr for autonomous dispatch. The default assumes in-cluster DNS:

```yaml
volundr:
  url: "http://volundr:8000"
```

See [connecting-ting-to-volundr.md](connecting-ting-to-volundr.md) for PAT setup.

#### PAT Signing Key

For development without Keycloak, Ting signs PATs locally with a symmetric key.
In production, use the `KeycloakTokenIssuer` instead (see `keycloak-pat-issuer.md`):

```yaml
auth:
  patSigningKey: "<same-key-envoy-validates>"
  patTtlDays: 365
```

---

## Database Migrations

Both Volundr and Ting run migrations via an init container using the
[`migrate`](https://github.com/golang-migrate/migrate) tool.

### How It Works

1. Migrations are embedded in a ConfigMap (`migrations-configmap.yaml` in each chart).
2. The init container mounts the ConfigMap and runs `migrate up`.
3. The `migrate` tool tracks applied versions in a `schema_migrations` table.

### Table Ownership Requirement

The `migrate` tool requires the database user to **own the tables it manages**.
If the DB user differs from the user that created the tables, migrations fail with
permission errors.

Fix ownership:

```sql
-- Replace 'volundr' with your DB user and 'volundr' with your DB name
ALTER DATABASE volundr OWNER TO volundr;

-- Reassign all objects in the public schema
REASSIGN OWNED BY old_user TO volundr;

-- Or individually:
ALTER TABLE schema_migrations OWNER TO volundr;
```

### Dirty Migration State

If a migration partially applies and then fails, the `schema_migrations` table
is left in a "dirty" state. The `migrate` tool refuses to run until this is resolved.

**Diagnose:**

```sql
SELECT version, dirty FROM schema_migrations;
```

**Fix:**

```sql
-- Option 1: Mark the failed version as clean (if the migration actually applied)
UPDATE schema_migrations SET dirty = false WHERE version = <version>;

-- Option 2: Roll back to the previous version (if the migration did not apply)
UPDATE schema_migrations SET version = <previous_version>, dirty = false;
```

Then re-run the deployment so the init container retries.

### Checking Migration State

To see how many migrations exist and which is latest, check the migration files
directly:

```bash
# Volundr migrations
ls migrations/*.up.sql | wc -l

# Ting migrations
ls migrations/ting/*.up.sql | wc -l
```

Or query the database:

```sql
SELECT version, dirty FROM schema_migrations;
```

---

## Common Failure Modes and Fixes

### 1. All API Calls Return 401 or HTML

**Cause:** Envoy sidecar is disabled (`envoy.enabled: false`) or JWT is not configured.

**Symptoms:** The web UI receives HTML (the web app's own index page) instead of JSON
from API endpoints. Bearer tokens are ignored.

**Fix:** Enable Envoy with JWT configuration:

```yaml
envoy:
  enabled: true
  jwt:
    enabled: true
    issuer: "https://keycloak.example.com/realms/volundr"
    audiences: [volundr-api]
    jwksUri: "https://keycloak.example.com/realms/volundr/protocol/openid-connect/certs"
    keycloakHost: "keycloak.default.svc.cluster.local"
```

### 2. Tracker Integrations Silently Fail (Linear, GitHub)

**Cause:** `MemoryCredentialStore` is the default — it stores nothing and returns no
credentials at runtime.

**Symptoms:** Creating integrations appears to succeed, but the adapters never resolve
credentials. No errors in logs — lookups simply return `None`.

**Fix:** Switch to a production credential store (Infisical or Vault):

```yaml
credentialStore:
  adapter: "niuu.adapters.infisical_credential_store.InfisicalCredentialStore"
  kwargs:
    site_url: "https://app.infisical.com"
    client_id: "<client-id>"
    project_id: "<project-id>"
  secretKwargs:
    - kwarg: client_secret
      secretName: infisical-auth
      secretKey: client-secret
```

### 3. Migration Init Container Fails

**Cause:** Table ownership mismatch or dirty migration state.

**Symptoms:** Pod stuck in `Init:CrashLoopBackOff`. Init container logs show
`error: Dirty database version <N>. Fix and force version.` or permission denied.

**Fix:**

```bash
# Check migration state
kubectl exec -it <postgres-pod> -- psql -U volundr -d volundr \
  -c "SELECT version, dirty FROM schema_migrations;"

# Fix dirty state
kubectl exec -it <postgres-pod> -- psql -U volundr -d volundr \
  -c "UPDATE schema_migrations SET dirty = false WHERE version = <N>;"

# Fix ownership
kubectl exec -it <postgres-pod> -- psql -U postgres -d volundr \
  -c "REASSIGN OWNED BY postgres TO volundr;"
```

### 4. Ting Cannot Dispatch Sessions to Volundr

**Cause:** No PAT stored in the credential store, or Volundr URL is wrong.

**Symptoms:** Dispatch attempts fail with authentication errors or connection refused.

**Fix:**

1. Verify `ting.volundr.url` resolves from inside the cluster.
2. Create a PAT in Volundr and store it as a Ting integration connection
   (see [connecting-ting-to-volundr.md](connecting-ting-to-volundr.md)).
3. Ensure the credential store is a production adapter (not `MemoryCredentialStore`).

### 5. JWKS Fetch Fails (Envoy Startup)

**Cause:** Envoy cannot reach Keycloak to fetch the JWKS.

**Symptoms:** Envoy logs show `JWKS fetch failed` or DNS resolution errors.
Requests pass through without JWT validation (fail-open) or are rejected (fail-close
depending on config).

**Fix:**

1. Verify `keycloakHost` is a resolvable hostname from inside the cluster
   (e.g., `keycloak.default.svc.cluster.local`).
2. Check `keycloakPort` matches the Keycloak service port (usually `8080` for HTTP,
   `8443` for HTTPS).
3. If using TLS, set `keycloakTls: true`.

### 6. Web UI Login Redirect Fails

**Cause:** OIDC configuration missing or wrong `clientId`.

**Symptoms:** Clicking "Login" does nothing or redirects to an error page.

**Fix:**

```yaml
web:
  config:
    oidc:
      authority: "https://keycloak.example.com/realms/volundr"
      clientId: "volundr-web"   # must match the Keycloak client
      scope: "openid profile email"
```

Ensure the `volundr-web` client in Keycloak has the correct redirect URIs
(e.g., `https://niuu.example.com/*`).

---

## Shared Dependencies Summary

Both Volundr and Ting require these shared infrastructure components:

| Component | Shared? | Notes |
|---|---|---|
| PostgreSQL cluster | Yes | Separate databases (`volundr`, `ting`) on the same cluster |
| Keycloak realm | Yes | Same realm, same JWKS endpoint |
| Credential store | Yes | Same Infisical/Vault backend and project |
| Ingress controller | Yes | Same host, path-based routing |
| TLS certificate | Yes | Single cert for the shared domain |
| PAT issuer client | Yes | Single `volundr-pat-issuer` Keycloak client |

---

## Quick Start (Minimal Production)

```bash
# 1. Create namespace
kubectl create namespace volundr

# 2. Create required secrets
kubectl create secret generic volundr-db -n volundr \
  --from-literal=username=volundr \
  --from-literal=password='<db-password>'

kubectl create secret generic ting-db -n volundr \
  --from-literal=username=ting \
  --from-literal=password='<db-password>'

kubectl create secret generic volundr-pat-issuer -n volundr \
  --from-literal=client-secret='<keycloak-client-secret>'

kubectl create secret generic infisical-auth -n volundr \
  --from-literal=client-id='<infisical-client-id>' \
  --from-literal=client-secret='<infisical-client-secret>'

# 3. Install via umbrella chart
helm install niuu ./charts/niuu -n volundr \
  -f production-values.yaml
```

See the [production checklist](../deployment/production.md) for additional hardening steps.
