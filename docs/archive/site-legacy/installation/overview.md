# Installation Overview

Pick the path that matches your goal.

| Goal | Path | Time |
|------|------|------|
| Try it out locally | CLI (`volundr init && volundr up`) | 5 min |
| Local Kubernetes | [k3d cluster + Helm](local-k3d.md) | 15 min |
| Single-node server (DGX Spark, homelab) | [k3s + Helm](single-node.md) | 20 min |
| Team / production | [Kubernetes cluster + Helm](kubernetes.md) | 30 min |

All Helm-based paths use the same chart. The difference is cluster setup and values.

---

## Prerequisites by Path

### CLI Local

- `volundr` binary
- Anthropic API key

### k3d (Local Kubernetes)

- Docker
- k3d
- kubectl
- Helm
- Anthropic API key

### Single-Node (k3s)

- Linux server (4+ GB RAM recommended)
- k3s
- kubectl
- Helm
- Anthropic API key
- PostgreSQL (in-cluster or external)

### Production Kubernetes

- Kubernetes 1.24+ cluster
- Helm 3.8+
- Storage class with ReadWriteMany support
- PostgreSQL 12+ (external recommended)
- Ingress controller (nginx, Traefik, etc.)
- Anthropic API key
- Optional: cert-manager, Gateway API controller, OIDC identity provider

---

## Hardware Requirements

| Component | RAM | CPU |
|-----------|-----|-----|
| API server | 256Mi -- 1Gi | 100m -- 1000m |
| Per session pod (Skuld + code-server + terminal) | 256Mi -- 3Gi | 100m -- 1500m |
| PostgreSQL | 256Mi+ | 100m+ |

**Storage:** RWX volume for shared sessions. 1Gi minimum recommended. Grows with the number of concurrent sessions and repository sizes.

---

## Next Steps

- [Local Development (k3d)](local-k3d.md) -- full Kubernetes on your laptop
- [Single-Node Deployment (k3s)](single-node.md) -- DGX Spark, homelab, bare metal
- [Production Kubernetes](kubernetes.md) -- team clusters with OIDC and TLS
- [Helm Values Reference](helm-reference.md) -- every configurable value
- [Production Checklist](production.md) -- go-live readiness
