# Keycloak PAT Issuer Setup

Personal Access Tokens (PATs) are issued via Keycloak Token Exchange so they're
signed by the IDP and accepted by Envoy natively.

## Prerequisites

- Keycloak 26+ with `token-exchange` feature enabled
- Admin access to the Keycloak realm

## 1. Enable Token Exchange Feature

The Keycloak instance must have `token-exchange` enabled. For the Keycloak
Operator, add to the `Keycloak` CR:

```yaml
spec:
  features:
    enabled:
      - token-exchange
      - admin-fine-grained-authz
```

## 2. Create the PAT Issuer Client

Create a confidential client in the Volundr realm:

| Setting | Value |
|---------|-------|
| Client ID | `volundr-pat-issuer` |
| Client type | OpenID Connect |
| Client authentication | ON (confidential) |
| Authorization | OFF |
| Standard flow | OFF |
| Direct access grants | OFF |
| Service accounts roles | ON |

### Service Account Roles

On the **Service account roles** tab:

1. Click **Assign role**
2. Switch filter to **Filter by clients**
3. Search for `realm-management`
4. Assign **`manage-users`**

## 3. Add Audience Mapper

On the **Client scopes** or **Protocol mappers** tab of `volundr-pat-issuer`:

Add a protocol mapper:

| Setting | Value |
|---------|-------|
| Name | `volundr-api-audience` |
| Mapper type | Audience |
| Included Client Audience | `volundr-api` |
| Add to ID token | OFF |
| Add to access token | ON |

This ensures exchanged tokens include `volundr-api` in the `aud` claim so
Envoy accepts them.

## 4. Set Token Lifespan

On the **Advanced** tab of `volundr-pat-issuer`:

| Setting | Value |
|---------|-------|
| Access Token Lifespan | `31536000` (365 days) |
| Client Session Max Lifespan | `31536000` |
| Client Session Idle Timeout | `31536000` |

### Realm SSO Session Max

The realm's `ssoSessionMaxLifespan` caps token exchange lifetimes. To allow
365-day PATs:

**Realm Settings → Sessions → SSO Session Max** = `31536000` seconds

> Normal browser sessions are still bounded by the SSO Session Idle timeout,
> which is typically much shorter (e.g. 10 hours).

## 5. Add Audience Mapper to Web and CLI Clients

Token exchange requires the subject token (user's browser/CLI token) to include
the `volundr-pat-issuer` client in its audience. Add an audience mapper to
both `volundr-web` and `volundr-cli`:

| Setting | Value |
|---------|-------|
| Name | `pat-issuer-audience` |
| Mapper type | Audience |
| Included Client Audience | `volundr-pat-issuer` |
| Add to ID token | OFF |
| Add to access token | ON |

## 6. Store the Client Secret

The client secret for `volundr-pat-issuer` must be available as a Kubernetes
secret in the Volundr namespace.

### Via Doppler + ExternalSecrets

Store the secret in Doppler:

```bash
doppler secrets set VOLUNDR_PAT_ISSUER_CLIENT_SECRET="<client-secret>" \
  --project bifrost --config prd
```

ExternalSecret manifest (in the infrastructure repo):

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: volundr-pat-issuer
  namespace: volundr
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: doppler-store
    kind: ClusterSecretStore
  target:
    name: volundr-pat-issuer
    creationPolicy: Owner
  data:
    - secretKey: client-secret
      remoteRef:
        key: VOLUNDR_PAT_ISSUER_CLIENT_SECRET
```

### Manual (dev/test)

```bash
kubectl create secret generic volundr-pat-issuer \
  --namespace volundr \
  --from-literal=client-secret="<client-secret>"
```

## 7. Configure Volundr and Tyr

### Helm Values

```yaml
# Both volundr and tyr sections:
pat:
  token_issuer_adapter: "niuu.adapters.keycloak_token_issuer.KeycloakTokenIssuer"
  token_issuer_kwargs:
    token_url: "https://keycloak.example.com/realms/volundr/protocol/openid-connect/token"
    client_id: "volundr-pat-issuer"
  issuerSecretName: "volundr-pat-issuer"
  issuerSecretKey: "client-secret"
```

### Environment Variables

The Helm chart injects the client secret via:

```
PAT__TOKEN_ISSUER_KWARGS__CLIENT_SECRET  →  secretKeyRef: volundr-pat-issuer/client-secret
```

For manual patching:

```bash
kubectl patch deployment niuu-volundr -n volundr --type=json -p='[
  {"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{
    "name":"PAT__TOKEN_ISSUER_ADAPTER",
    "value":"niuu.adapters.keycloak_token_issuer.KeycloakTokenIssuer"
  }},
  {"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{
    "name":"PAT__TOKEN_ISSUER_KWARGS__TOKEN_URL",
    "value":"https://keycloak.example.com/realms/volundr/protocol/openid-connect/token"
  }},
  {"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{
    "name":"PAT__TOKEN_ISSUER_KWARGS__CLIENT_ID",
    "value":"volundr-pat-issuer"
  }},
  {"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{
    "name":"PAT__TOKEN_ISSUER_KWARGS__CLIENT_SECRET",
    "valueFrom":{"secretKeyRef":{"name":"volundr-pat-issuer","key":"client-secret"}}
  }}
]'
```

## Token Exchange Flow

```
User authenticates via Keycloak (browser/CLI)
    ↓ JWT with aud: [volundr-api, volundr-pat-issuer]
User clicks "Create Token" in Volundr UI
    ↓ POST /api/v1/tokens (Authorization: Bearer <user-jwt>)
Volundr extracts user's JWT from request
    ↓ PATService.create(subject_token=<user-jwt>)
KeycloakTokenIssuer calls Keycloak Token Exchange
    ↓ grant_type=urn:ietf:params:oauth:grant-type:token-exchange
    ↓ subject_token=<user-jwt>
    ↓ client_id=volundr-pat-issuer, client_secret=<secret>
Keycloak returns new JWT (365-day, aud: volundr-api)
    ↓ Signed by Keycloak's RSA key
Volundr stores SHA-256 hash in DB, returns raw JWT once
    ↓ User stores the PAT
User uses PAT for API calls
    ↓ Authorization: Bearer <pat-jwt>
Envoy validates signature against Keycloak JWKS ✓
    ↓ Extracts claims, forwards headers
Volundr PAT middleware checks revocation status ✓
    ↓ Request proceeds
```

## Fallback: MemoryTokenIssuer (Dev Only)

For local development without Keycloak, the default adapter issues HS256-signed
JWTs locally. These won't be accepted by Envoy:

```yaml
pat:
  token_issuer_adapter: "niuu.adapters.memory_token_issuer.MemoryTokenIssuer"
  token_issuer_kwargs:
    signing_key: "dev-only-key"
```
