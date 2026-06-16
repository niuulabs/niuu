---
type: entity
confidence: high
entity_type: project
related_entities: [sigrid-dahl, freya-larsen]
source_ids: [src_c1b2c3d4e5f60202]
---

# Midgard dashboard

## Compiled Truth

### Key Facts
- Midgard is the operator dashboard rebuild: live fleet map, telemetry streams, and alert triage in one screen.
- Design led by Sigrid Dahl, sponsored by Freya Larsen.
- The interface brief is information-dense and keyboard-first, based on twelve operator interviews.
- Streams live data over WebSockets through the Envoy gateway.
- Target ship date is end of Q3 2026.

### Relationships
- [[sigrid-dahl]] — design lead.
- [[freya-larsen]] — executive sponsor.

### Assessment
The riskiest part is the live-streaming layer under flaky field connectivity;
the prototype handles reconnects but has not been tested at full fleet scale.

## Timeline

- 2026-02-18: Rebuild kicked off. [Source: kickoff notes, meeting, 2026-02-18]
- 2026-04-29: Operator interview round completed; brief updated. [Source: research notes, drive, 2026-04-29]
- 2026-05-27: First live prototype demoed to the Helios operators. [Source: demo recording, drive, 2026-05-27]
