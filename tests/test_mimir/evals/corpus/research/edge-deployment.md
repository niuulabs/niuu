---
type: topic
confidence: high
related_entities: [rune-lindqvist, orchard]
source_ids: [src_d1b2c3d4e5f60304]
---

# Edge deployment research

## Compiled Truth

### Key Facts
- Remote sites (wind farms, orchards) need on-site inference because connectivity drops below usable for minutes at a time.
- Rune benchmarked five single-board computers; the winner runs the vision model at 22 frames per second under a 15-watt envelope.
- The offline-first field tablet syncs opportunistically and never blocks an operator on the network.
- The Orchard robot reuses the same edge stack: identical board, identical model-serving runtime.

### Relationships
- [[rune-lindqvist]] — ran the benchmarks and built the prototype.
- [[orchard]] — consumes the edge inference stack on the picking robot.

### Assessment
The 15-watt envelope is the real finding — it means solar-charged enclosures
work at unattended sites. Hardware line in the 2026 budget is sized from
these numbers.

## Timeline

- 2026-01-08: Engagement started. [Source: contract, drive, 2026-01-08]
- 2026-03-30: Single-board benchmark report delivered. [Source: rune, research notes, 2026-03-30]
- 2026-05-11: Field tablet validated at the Skagen site. [Source: field report, drive, 2026-05-11]
