# Session Gateway

Controls how session traffic is routed. Two approaches depending on your infrastructure.

## Ingress-per-Session (simpler, default)

Each session gets its own Ingress resource. Session URL: `https://<session-name>.<base_domain>/`

```yaml
sessionContributors:
  - adapter: "volundr.adapters.outbound.contributors.core.CoreSessionContributor"
    kwargs:
      base_domain: "sessions.example.com"
      ingress_enabled: true
```

This works with any ingress controller (nginx, Traefik, etc). No extra infrastructure needed.

## Gateway API (production, centralized)

A shared Gateway with per-session HTTPRoutes and SecurityPolicies. Requires Envoy Gateway controller.

```yaml
sessionGateway:
  enabled: true
  name: "volundr-gateway"
  gatewayClassName: "eg"
  hostname: "sessions.example.com"
  certIssuer: "letsencrypt-prod"
  httpRedirect: true

gateway:
  adapter: "volundr.adapters.outbound.k8s_gateway.K8sGatewayAdapter"
  kwargs:
    namespace: "volundr-sessions"
    gateway_name: "volundr-gateway"
    gateway_namespace: "volundr"
    gateway_domain: "sessions.example.com"
    issuer_url: "https://idp.example.com"
    audience: "volundr"
```

### Features with Gateway API

- Wildcard TLS via cert-manager
- JWT validation on session routes via SecurityPolicy
- Automatic DNS via external-dns
- HTTP-to-HTTPS redirect

## Port: GatewayPort

| Adapter | Description |
|---------|-------------|
| `InMemoryGatewayAdapter` | Development — returns static config |
| `K8sGatewayAdapter` | Production — manages HTTPRoute and SecurityPolicy resources |

The GatewayContributor (in the session contributor pipeline) calls the GatewayPort to create routing resources when a session starts, and removes them on cleanup.
