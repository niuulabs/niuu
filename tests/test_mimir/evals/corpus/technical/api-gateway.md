---
type: topic
confidence: high
related_entities: [freya-larsen, astrid-nilsen]
source_ids: [src_b1b2c3d4e5f60105]
---

# API gateway

## Compiled Truth

### Key Facts
- A single Envoy gateway fronts every public API; services are never exposed directly.
- The gateway terminates TLS, validates OIDC JWTs, enforces rate limits, and strips internal headers.
- Routing configuration is generated from service annotations, not edited by hand.
- WebSocket upgrades for the live dashboard pass through the same gateway with sticky sessions.

### Relationships
- [[freya-larsen]] — selected Envoy as part of the platform consolidation.
- [[astrid-nilsen]] — owns the gateway security policy and header-stripping rules.

### Assessment
The gateway is the chokepoint that makes the no-custom-auth rule enforceable:
if a service tries to do its own thing, traffic simply never reaches it.

## Timeline

- 2025-04-18: Envoy gateway replaced the per-service nginx sidecars. [Source: rfc-008, wiki, 2025-04-18]
- 2025-09-03: JWT validation moved to the gateway as part of the OIDC decision. [Source: architecture review, drive, 2025-09-03]
- 2026-01-22: Header-stripping rules tightened after Astrid's vendor SDK finding. [Source: security review, wiki, 2026-01-22]
