# Production Checklist

Use this checklist before going live. Items are grouped by priority.

---

## Required

- [ ] External PostgreSQL provisioned and accessible from the cluster
- [ ] Database migrations applied (enabled by default via init container)
- [ ] Database credentials stored in a Kubernetes secret (`volundr-db`)
- [ ] Anthropic API key stored in a Kubernetes secret (`volundr-anthropic-api`)
- [ ] Ingress configured with TLS termination
- [ ] Identity adapter set to `EnvoyHeaderIdentityAdapter` (not `AllowAll`)
- [ ] Authorization adapter set to `SimpleRoleAuthorizationAdapter` or `CerbosAuthorizationAdapter`
- [ ] Storage class supports ReadWriteMany for sessions and home PVCs

---

## Security

- [ ] OIDC/OAuth2 configured via Envoy sidecar with JWT validation
- [ ] Gateway API security policies enforce JWT validation on session routes
- [ ] Credential store uses Vault or Infisical (not in-memory)
- [ ] Secret injection uses CSI driver (not in-memory)
- [ ] Git tokens stored in Kubernetes secrets, referenced by `existingSecret`
- [ ] Pod security context enforces `runAsNonRoot`, drops all capabilities
- [ ] Container runs with read-only root filesystem
- [ ] CORS origins restricted to your domain (not `*`)

---

## Recommended

- [ ] HPA enabled with appropriate min/max replicas
- [ ] PDB configured to maintain availability during rollouts
- [ ] NetworkPolicy restricting pod-to-pod traffic
- [ ] Resource requests and limits set for API and session pods
- [ ] Kyverno PVC isolation policy applied (prevents cross-session access)
- [ ] Log format set to `json` for structured logging
- [ ] Database pool size tuned for replica count (`maxPoolSize * replicaCount <= max_connections`)

---

## Monitoring

Volundr exposes the following endpoints:

| Endpoint | Purpose |
|----------|---------|
| `/health` | Liveness and readiness probes |
| `/api/v1/forge/sessions/stream` | SSE stream for real-time session state |
| `/api/v1/forge/events/health` | Event pipeline and sink status |

OpenTelemetry traces and metrics are available when an OTel collector is configured.

---

## Scaling

- **API** is stateless. Scale horizontally via HPA.
- **SSE connections** are per-instance (in-memory broadcaster). Use sticky sessions or a single replica for SSE. For multi-replica SSE, add an external pub/sub layer.
- **Skuld brokers** run one-per-session. They scale with session count.
- **Database pool size** should account for all replicas: `maxPoolSize * replicaCount` must not exceed PostgreSQL `max_connections`.
