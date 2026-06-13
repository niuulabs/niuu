---
type: entity
confidence: high
entity_type: person
related_entities: [asgard-robotics, bjorn-eriksen]
source_ids: [src_a1b2c3d4e5f60003]
---

# Astrid Nilsen

## Compiled Truth

### Key Facts
- Security engineer at Asgard Robotics; owns incident response and the on-call rotation.
- Maintains the SSRF protections on the ingest endpoints and reviews all new external integrations.
- Co-authored the March 2026 database outage postmortem with Bjorn.
- Pushing to move all service-to-service auth to client-credentials OIDC flows.

### Relationships
- [[asgard-robotics]] — security engineer.
- [[bjorn-eriksen]] — incident response partner during the March outage.
- [[freya-larsen]] — aligned with Freya on the no-custom-auth principle.

### Assessment
The most security-conservative voice in the team. Her reviews are slow but
have caught two real vulnerabilities in vendor SDKs this year.

## Timeline

- 2025-04-10: Joined as the first dedicated security hire. [Source: announcement, slack-#general, 2025-04-10]
- 2026-01-22: Flagged a token-leak vulnerability in a vendor SDK. [Source: astrid, security review, 2026-01-22]
- 2026-03-14: Ran incident command for the database outage. [Source: incident channel, slack-#incident, 2026-03-14]
- 2026-05-19: Shipped the new on-call escalation policy. [Source: astrid, wiki, 2026-05-19]
