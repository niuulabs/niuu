# Local Development (k3d)

k3d runs a lightweight k3s cluster inside Docker containers. Full Kubernetes, no VM overhead. Good for testing Helm values before deploying to a real cluster.

---

## Prerequisites

```bash
# Docker
docker info

# k3d
k3d version
# Install: curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash

# kubectl
kubectl version --client

# Helm
helm version
```

---

## Create Cluster

```bash
k3d cluster create volundr \
  --port "8080:80@loadbalancer" \
  --port "8443:443@loadbalancer"
```

This maps port 8080 on your machine to the cluster's ingress controller.

---

## Create Namespace and Secrets

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
  --from-literal=password=volundr

# GitHub token (optional)
kubectl create secret generic github-token \
  -n volundr \
  --from-literal=token=ghp_xxx
```

---

## Deploy PostgreSQL

In-cluster PostgreSQL for development:

```bash
helm install postgresql oci://registry-1.docker.io/bitnamicharts/postgresql \
  -n volundr \
  --set auth.username=volundr \
  --set auth.password=volundr \
  --set auth.database=volundr
```

---

## Deploy Volundr

```bash
helm install volundr oci://ghcr.io/niuulabs/charts/volundr \
  -n volundr \
  --set database.external.host=postgresql.volundr.svc.cluster.local \
  --set database.existingSecret=volundr-db \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=localhost \
  --set ingress.hosts[0].paths[0].path=/api/v1 \
  --set ingress.hosts[0].paths[0].pathType=Prefix
```

---

## Verify

```bash
kubectl get pods -n volundr
curl http://localhost:8080/health
```

Both the API pod and PostgreSQL pod should be `Running`. The health endpoint returns 200 when the API is ready.

---

## What You Get

Full Kubernetes deployment with sessions, pods, PVCs -- identical to production. The same Helm chart, same container images, same session lifecycle.

## What's Different from Production

- No TLS
- No OIDC (uses `AllowAllIdentityAdapter`)
- In-cluster PostgreSQL (not managed/external)
- No persistent storage class (uses k3d's built-in local-path)

---

## Cleanup

```bash
k3d cluster delete volundr
```
