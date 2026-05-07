# Production Kubernetes

For teams running Volundr on an existing Kubernetes cluster.

---

## Cluster Requirements

- Kubernetes 1.24+
- Ingress controller (nginx, Traefik, HAProxy, or Gateway API)
- Storage class with ReadWriteMany support (Longhorn, NFS, EFS, etc.)
- External PostgreSQL 12+ (recommended over in-cluster)

## Optional Cluster Components

- **cert-manager** -- automatic TLS certificate provisioning
- **Envoy Gateway** -- Gateway API-based routing with JWT validation
- **Kyverno** -- PVC isolation policies
- **External DNS** -- automatic DNS record management

---

## Step 1: Namespace and Secrets

```bash
kubectl create namespace volundr

# Anthropic API key
kubectl create secret generic volundr-anthropic-api \
  -n volundr \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-xxx

# Database credentials
kubectl create secret generic volundr-db \
  -n volundr \
  --from-literal=username=volundr \
  --from-literal=password=<your-db-password>

# GitHub token (optional)
kubectl create secret generic github-token \
  -n volundr \
  --from-literal=token=ghp_xxx
```

---

## Step 2: Database

Use a dedicated PostgreSQL instance. For production, the [CloudNativePG](https://cloudnative-pg.io/) operator (CNCF project) is the recommended way to run PostgreSQL on Kubernetes.

```bash
# Install CloudNativePG operator
kubectl apply --server-side -f \
  https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.25/releases/cnpg-1.25.1.yaml
```

Create a PostgreSQL cluster:

```yaml
# postgres-cluster.yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: volundr-pg
  namespace: volundr
spec:
  instances: 2
  storage:
    size: 10Gi
  bootstrap:
    initdb:
      database: volundr
      owner: volundr
```

```bash
kubectl apply -f postgres-cluster.yaml
```

Then point Volundr at it:

```yaml
# values-production.yaml
database:
  external:
    enabled: true
    host: volundr-pg-rw.volundr.svc.cluster.local
    port: 5432
  existingSecret: volundr-db
```

---

## Step 3: Identity and Authorization

Configure OIDC via the Envoy sidecar:

```yaml
envoy:
  enabled: true
  jwt:
    enabled: true
    issuer: "https://keycloak.example.com/realms/volundr"
    audiences:
      - volundr
    jwksUri: "https://keycloak.example.com/realms/volundr/protocol/openid-connect/certs"
    keycloakHost: keycloak.default.svc.cluster.local
    keycloakPort: 8080

identity:
  adapter: "volundr.adapters.outbound.identity.EnvoyHeaderIdentityAdapter"
  kwargs:
    user_id_header: "x-auth-user-id"
    email_header: "x-auth-email"

authorization:
  adapter: "volundr.adapters.outbound.authorization.SimpleRoleAuthorizationAdapter"
```

The Envoy sidecar validates JWTs and extracts claims into trusted headers. The identity adapter reads those headers. No custom auth code needed.

---

## Step 4: Storage

```yaml
storage:
  sessions:
    enabled: true
    storageClass: longhorn  # or your RWX storage class
    accessMode: ReadWriteMany
    size: 10Gi
  home:
    enabled: true
    storageClass: longhorn
    accessMode: ReadWriteMany
    size: 5Gi
```

Sessions and home directories are shared between the API server and session pods. ReadWriteMany is required.

---

## Step 5: Ingress

```yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
  hosts:
    - host: volundr.example.com
      paths:
        - path: /api/v1
          pathType: Prefix
  tls:
    - secretName: volundr-tls
      hosts:
        - volundr.example.com
```

Long timeouts are needed for SSE streams and WebSocket connections to session pods.

---

## Step 6: Deploy

```bash
helm install volundr oci://ghcr.io/niuulabs/charts/volundr \
  -n volundr --create-namespace \
  -f values-production.yaml
```

---

## Step 7: Verify

```bash
kubectl get pods -n volundr
kubectl logs -n volundr deployment/volundr
curl https://volundr.example.com/health
```

---

## Web UI

Deploy the web frontend alongside the API:

```yaml
web:
  enabled: true
  ingress:
    enabled: true
    hosts:
      - host: volundr.example.com
        paths:
          - path: /
            pathType: Prefix
  config:
    oidc:
      authority: "https://keycloak.example.com/realms/volundr"
      clientId: "volundr-web"
```

The web UI and API share the same domain. The API is served at `/api/v1/*`, the UI at `/`.

---

## Scaling

- **API** is stateless. Use HPA to scale horizontally.
- **SSE connections** are per-instance (in-memory broadcaster). Use sticky sessions or a single replica for SSE, or consider external pub/sub for multi-replica SSE.
- **Skuld brokers** scale with session count (one per session).
- **Database pool size** should match total connections across all API replicas. Default: `minPoolSize: 5`, `maxPoolSize: 20` per replica.
