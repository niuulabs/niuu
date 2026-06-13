---
type: decision
confidence: high
related_entities: [freya-larsen, astrid-nilsen]
source_ids: [src_b1b2c3d4e5f60101]
---

# Authentication: OIDC everywhere

## Compiled Truth

### Key Facts
- All user login flows use standard OIDC authorization-code with PKCE; no custom token layers.
- Service-to-service calls use the OIDC client-credentials grant.
- The Envoy gateway validates JWTs at the edge; services never parse tokens themselves.
- Identity provider is abstracted behind an adapter so Keycloak can be swapped for Entra ID per customer.
- Sign-in sessions last eight hours; refresh happens silently in the SPA.

### Relationships
- [[freya-larsen]] — made the decision; it implements her no-custom-auth principle.
- [[astrid-nilsen]] — reviewed the flows and owns ongoing security review.

### Assessment
The decision removed three bespoke token implementations. The only friction
is local development, where running a full identity provider is heavy — the
mitigation is a dev-mode issuer container, not a bypass flag.

## Timeline

- 2025-08-21: Architecture review proposed standardising login on OIDC. [Source: rfc-014, wiki, 2025-08-21]
- 2025-09-03: Decision approved by Freya. [Source: architecture review minutes, drive, 2025-09-03]
- 2025-11-30: Last custom token code deleted from the gateway. [Source: changelog, git, 2025-11-30]
