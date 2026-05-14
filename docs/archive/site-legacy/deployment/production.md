# Production Checklist

## Required

- [ ] PostgreSQL database provisioned and accessible (databases: `volundr`, `ting`)
- [ ] Database migrations applied (both Volundr and Ting)
- [ ] `DATABASE__PASSWORD` set via Kubernetes secret
- [ ] Ingress configured with TLS (shared domain for Volundr, Ting, and web UI)
- [ ] Identity adapter set to `EnvoyHeaderIdentityAdapter` (not AllowAll)
- [ ] Authorization adapter set to `CerbosAuthorizationAdapter` or `SimpleRoleAuthorizationAdapter`
- [ ] Envoy sidecar enabled with JWT config pointing at Keycloak (both Volundr and Ting)
- [ ] Credential store configured (Infisical or Vault) if tracker integrations are needed

For the full prerequisites checklist, see the
[deployment prerequisites guide](../setup/deployment.md).

## Recommended

- [ ] HPA enabled with appropriate min/max replicas
- [ ] PDB configured to maintain availability during rollouts
- [ ] NetworkPolicy restricting pod-to-pod traffic
- [ ] Resource requests and limits set
- [ ] Kyverno PVC isolation policy applied
- [ ] Log format set to `json` for structured logging
- [ ] OpenTelemetry sink enabled for observability

## Security

- [ ] OIDC/OAuth2 configured via Envoy sidecar
- [ ] Gateway API security policies enforce JWT validation on session routes
- [ ] Credential store uses Vault or Infisical (not in-memory)
- [ ] Secret injection uses CSI driver (not in-memory)
- [ ] Git tokens stored in Kubernetes secrets, referenced by `token_env`

## Monitoring

Volundr exposes:

- `/health` — liveness/readiness probe
- SSE stream at `/api/v1/forge/sessions/stream` — real-time state
- Event pipeline health at `/api/v1/forge/events/health` — sink status
- OpenTelemetry traces and metrics (when enabled)

## Scaling

- Volundr API is stateless — scale horizontally via HPA
- SSE connections are per-instance (in-memory broadcaster) — consider sticky sessions or external pub/sub for multi-replica SSE
- Skuld brokers run one-per-session — they scale with session count
- Database pool size should match total connections across all API replicas
