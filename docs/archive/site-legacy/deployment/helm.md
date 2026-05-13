# Helm Deployment

Volundr ships three Helm charts, plus an umbrella chart:

- `charts/niuu/` — umbrella chart that deploys Volundr and Tyr together
- `charts/volundr/` — the API server and optional web UI
- `charts/tyr/` — the saga coordinator for multi-repo campaigns
- `charts/skuld/` — the WebSocket broker (deployed per-session by the pod manager)

For full infrastructure prerequisites, see the
[deployment prerequisites guide](../setup/deployment.md).

## Install

```bash
helm install volundr ./charts/volundr -n volundr --create-namespace \
  --set database.external.host=postgres.svc.cluster.local \
  --set database.external.password=secret \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=volundr.example.com
```

## Volundr chart resources

The chart creates:

| Resource | Description |
|----------|-------------|
| Deployment | API server pods |
| Service | ClusterIP service |
| Service (internal) | Internal service for pod-to-pod |
| Ingress | Optional ingress |
| ConfigMap | Application config |
| ConfigMap (Envoy) | Envoy sidecar config |
| ConfigMap (migrations) | Database migrations |
| Secret | Database credentials, API keys |
| HPA | Horizontal pod autoscaler |
| PDB | Pod disruption budget |
| NetworkPolicy | Network isolation |
| ServiceAccount + RBAC | Kubernetes permissions |
| PVC (sessions) | Shared session storage |
| PVC (home) | User home directory storage |
| Gateway | Gateway API resources |
| Kyverno policy | PVC isolation enforcement |
| Session definitions | Skuld + Code Server pod templates |

## Skuld chart resources

| Resource | Description |
|----------|-------------|
| Deployment | Broker pod |
| Service | WebSocket endpoint |
| ConfigMap | Skuld configuration |
| ConfigMap (Envoy) | Auth sidecar |
| ConfigMap (nginx) | Static file serving |
| Ingress / HTTPRoute | Session routing |
| SecurityPolicy | JWT validation |

## Key values

### Database

```yaml
database:
  external:
    host: postgres.svc.cluster.local
    port: 5432
    user: volundr
    password: ""
    name: volundr
```

### Git providers

```yaml
git:
  github:
    enabled: true
    instances:
      - name: GitHub
        baseUrl: https://api.github.com
        secretName: github-token  # K8s secret reference
        orgs:
          - my-org
```

### Ingress

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: volundr.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: volundr-tls
      hosts:
        - volundr.example.com
```

### Gateway API

```yaml
gateway:
  enabled: true
  name: volundr-gateway
  namespace: volundr-system
  domain: sessions.example.com
```

### Resources

```yaml
resources:
  requests:
    cpu: 250m
    memory: 512Mi
  limits:
    cpu: "1"
    memory: 1Gi
```
