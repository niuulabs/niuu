# AI-Agent Setup Guide

This guide is designed for AI coding agents (Claude Code, OpenClaw, etc.) to follow
step-by-step when deploying Volundr. Every step includes a command and expected output
so an agent can verify success before proceeding.

---

## Phase 0: Prerequisites

Verify that each required tool is available before continuing.

| Tool | Check command | Install |
|------|--------------|---------|
| kubectl | `kubectl version --client` | See k3s install or cloud provider docs |
| helm | `helm version` | `curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \| bash` |
| k3s (single-node) | `k3s --version` | `curl -sfL https://get.k3s.io \| sh -` |
| PostgreSQL client | `psql --version` | `apt install postgresql-client` or `brew install postgresql` |

Run each check command. If a tool is missing, use the install command. k3s is only
required for single-node deployments.

---

## Phase 1: Gather Information

Before starting, the agent should ask the operator these questions:

```
1. DEPLOYMENT TARGET: Where will Volundr run?
   - [ ] Single machine (k3s) → Phase 2A
   - [ ] Existing Kubernetes cluster → Phase 2B

2. DOMAIN: What domain/hostname?
   Default: volundr.local
   Used in: ingress.hosts[0].host, sessionContributors[0].kwargs.base_domain

3. DATABASE: PostgreSQL setup?
   - [ ] Deploy in-cluster (simple, not production-grade)
   - [ ] External database (provide: host, port, user, password, name)

4. ANTHROPIC API KEY: Required for Claude Code sessions.
   Value: sk-ant-...

5. GIT PROVIDER: Which providers?
   - [ ] GitHub (provide: token, orgs)
   - [ ] GitLab (provide: token, groups)
   - [ ] Both
   - [ ] None (skip git integration)

6. STORAGE CLASS: What RWX storage class is available?
   k3s default: local-path
   Production: longhorn, nfs, efs, etc.

7. AUTH: Identity provider?
   - [ ] None (AllowAll for dev/testing)
   - [ ] OIDC (provide: issuer URL, client ID, JWKS URI)

8. TLS: Need HTTPS?
   - [ ] No (local/dev)
   - [ ] Yes (provide: cert-manager issuer or TLS secret name)

9. GPU: GPU workloads needed?
   - [ ] No
   - [ ] Yes (provide: GPU type, e.g., nvidia.com/gpu)
```

Record all answers before continuing. They are used to generate the Helm values file
in Phase 5.

---

## Phase 2A: Single-Node Setup (k3s)

Use this phase when deploying to a single machine.

### Step 1: Install k3s

```bash
curl -sfL https://get.k3s.io | sh -
```

Expected output:

```
[INFO]  systemd: Starting k3s
```

### Step 2: Configure kubeconfig

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
```

### Step 3: Verify cluster

```bash
kubectl get nodes
```

Expected output:

```
NAME     STATUS   ROLES                  AGE   VERSION
<host>   Ready    control-plane,master   <age> v1.x.x+k3s1
```

Proceed to Phase 3.

---

## Phase 2B: Existing Cluster Setup

Use this phase when deploying to an existing Kubernetes cluster. Skip k3s installation.

### Step 1: Verify cluster access

```bash
kubectl cluster-info
```

Expected output:

```
Kubernetes control plane is running at https://<api-server>
```

### Step 2: Verify node readiness

```bash
kubectl get nodes
```

Expected: all nodes show `STATUS=Ready`.

Proceed to Phase 3.

---

## Phase 3: Create Secrets

### Step 1: Create namespace

```bash
kubectl create namespace volundr
```

Expected output:

```
namespace/volundr created
```

### Step 2: Create Anthropic API key secret

```bash
kubectl create secret generic volundr-anthropic-api \
  -n volundr \
  --from-literal=ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
```

Expected output:

```
secret/volundr-anthropic-api created
```

### Step 3: Create database credentials secret

```bash
kubectl create secret generic volundr-db \
  -n volundr \
  --from-literal=username=volundr \
  --from-literal=password=${DB_PASSWORD}
```

Expected output:

```
secret/volundr-db created
```

### Step 4: Create Git token secret (if applicable)

For GitHub:

```bash
kubectl create secret generic github-token \
  -n volundr \
  --from-literal=token=${GITHUB_TOKEN}
```

Expected output:

```
secret/github-token created
```

For GitLab:

```bash
kubectl create secret generic gitlab-token \
  -n volundr \
  --from-literal=token=${GITLAB_TOKEN}
```

Expected output:

```
secret/gitlab-token created
```

Skip this step if no Git integration is needed.

---

## Phase 4: Database

### Option A: In-cluster PostgreSQL

```bash
helm install postgresql oci://registry-1.docker.io/bitnamicharts/postgresql \
  -n volundr \
  --set auth.username=volundr \
  --set auth.password=${DB_PASSWORD} \
  --set auth.database=volundr
```

Wait for the pod to become ready (typically 60 seconds):

```bash
kubectl wait --for=condition=Ready pod/postgresql-0 -n volundr --timeout=120s
```

Verify:

```bash
kubectl get pods -n volundr
```

Expected output:

```
NAME            READY   STATUS    RESTARTS   AGE
postgresql-0    1/1     Running   0          <age>
```

The in-cluster database host is `postgresql.volundr.svc.cluster.local`.

### Option B: External PostgreSQL

Verify connectivity from within the cluster:

```bash
kubectl run pg-test --rm -it --restart=Never -n volundr \
  --image=postgres:16 -- \
  psql "postgresql://volundr:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/volundr" -c "SELECT 1"
```

Expected: the query returns `1` and the pod terminates.

---

## Phase 5: Deploy Volundr

### Step 1: Generate values file

Create `values-generated.yaml` using the answers gathered in Phase 1. Use the
template below, replacing `${...}` variables with actual values.

```yaml
# values-generated.yaml
database:
  external:
    enabled: true
    host: ${DB_HOST}  # postgresql.volundr.svc.cluster.local for in-cluster
    port: 5432
  existingSecret: volundr-db

ingress:
  enabled: true
  className: ${INGRESS_CLASS}  # traefik for k3s, nginx for most clusters
  hosts:
    - host: ${DOMAIN}
      paths:
        - path: /api/v1
          pathType: Prefix

storage:
  sessions:
    storageClass: ${STORAGE_CLASS}
  home:
    storageClass: ${STORAGE_CLASS}

# Git integration (include only the providers that are enabled)
git:
  github:
    enabled: ${GITHUB_ENABLED}      # true or false
    existingSecret: github-token
  gitlab:
    enabled: ${GITLAB_ENABLED}      # true or false
    existingSecret: gitlab-token

# Identity / Auth
# Option 1: No auth (dev/testing only)
identity:
  adapter: "volundr.adapters.inbound.identity.allow_all.AllowAllIdentityAdapter"

# Option 2: OIDC (production)
# identity:
#   adapter: "volundr.adapters.inbound.identity.oidc.OIDCIdentityAdapter"
#   kwargs:
#     issuer: "${OIDC_ISSUER}"
#     client_id: "${OIDC_CLIENT_ID}"
#     jwks_uri: "${OIDC_JWKS_URI}"

sessionContributors:
  - adapter: "volundr.adapters.outbound.contributors.core.CoreSessionContributor"
    kwargs:
      base_domain: "${DOMAIN}"
      ingress_enabled: true
```

### Step 2: Install the Helm chart

```bash
helm install volundr oci://ghcr.io/niuulabs/charts/volundr \
  -n volundr \
  -f values-generated.yaml
```

### Step 3: Wait for deployment

```bash
kubectl wait --for=condition=Available deployment/volundr -n volundr --timeout=180s
```

### Step 4: Verify pods

```bash
kubectl get pods -n volundr
```

Expected output:

```
NAME                       READY   STATUS    RESTARTS   AGE
postgresql-0               1/1     Running   0          <age>
volundr-<hash>             1/1     Running   0          <age>
```

All pods should show `STATUS=Running` and `READY` columns should be complete
(e.g., `1/1` or `2/2`).

---

## Phase 6: Verification Checklist

Run each check in order. All must pass before the deployment is considered complete.

### 1. All pods running

```bash
kubectl get pods -n volundr
```

Expected: all pods show `STATUS=Running`, `READY` is complete.

### 2. API health check

```bash
curl -s http://${DOMAIN}/health | jq .
```

Expected output:

```json
{"status": "healthy"}
```

### 3. Migrations completed

```bash
kubectl logs -n volundr deployment/volundr -c migrate
```

Expected: no errors. Output shows `no change` or lists applied migrations.

### 4. Create a test session

```bash
curl -s -X POST http://${DOMAIN}/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"name": "test-session", "model": "claude-sonnet-4-20250514"}' | jq .
```

Expected: JSON response containing a session ID and `"status": "created"`.

### 5. Web UI loads (if enabled)

```bash
curl -s -o /dev/null -w "%{http_code}" http://${DOMAIN}/
```

Expected: `200`.

---

## Troubleshooting

### Pod stuck in Pending

```bash
kubectl describe pod <pod-name> -n volundr
```

Common causes: storage class does not exist, insufficient CPU/memory resources,
or node selector/toleration mismatch.

### CrashLoopBackOff

```bash
kubectl logs <pod-name> -n volundr
```

Common causes: database connection refused (wrong host or credentials), missing
secret, or invalid configuration in values file.

### Ingress not working

```bash
kubectl get ingress -n volundr
kubectl describe ingress -n volundr
```

Verify the ingress class name matches the installed ingress controller. For k3s
the default is `traefik`. Check controller logs:

```bash
# k3s / traefik
kubectl logs -n kube-system -l app.kubernetes.io/name=traefik

# nginx ingress
kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx
```

### Migration failed

```bash
kubectl logs <pod-name> -n volundr -c migrate
```

Common causes: database not reachable at the time the init container ran, or a
schema conflict from a previous partial migration. Fix the underlying issue and
delete the pod to let it restart (the migration init container will re-run).

### Secret not found

```bash
kubectl get secrets -n volundr
```

Verify all required secrets exist. Re-create any missing secrets using the
commands from Phase 3.
