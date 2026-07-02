# Current Momentum Resident State

- updated_at: 2026-06-28T10:00:00+00:00

## Current Beliefs

- Reflected Momentum state should guide the next signal selected for extraction.

## Constraints

- Attention selection must prefer signals that address current open tensions.

## Corrections

- none

## Open Tensions

- Carry reflected state into attention selection
  - id: tension-carry-reflected-state-into-attention
  - status: open
  - summary: Prove the next selected resident signal is chosen because it addresses current Momentum state, then carry the generated judgment into an executor handoff that inspects filesystem-backed resident artifacts and writes a bounded evidence artifact under resident handoff evidence.
  - evidence_refs: resident/continuation/momentum/runs/proof/reflections/reflection-proof.md
  - source_refs: resident/continuation/momentum/state/patches/patch-proof.md

## Stale Assumptions Or Unknowns

- none

## Recent Lessons

- Current-state handoff is only useful if future attention can see and apply it, then hand a generated brief to an executor that can inspect resident artifacts in its configured workspace and create requested evidence inside that workspace.

## Candidate Reflexes (candidate-only)

- none

## Candidate Capability Gaps (candidate-only)

- none

## Source Refs

- resident/continuation/momentum/state/patches/patch-proof.md

## State Data

```json
{
  "beliefs": [
    "Reflected Momentum state should guide the next signal selected for extraction."
  ],
  "constraints": [
    "Attention selection must prefer signals that address current open tensions."
  ],
  "corrections": [],
  "open_tensions": [
    {
      "tension_id": "tension-carry-reflected-state-into-attention",
      "title": "Carry reflected state into attention selection",
      "summary": "Prove the next selected resident signal is chosen because it addresses current Momentum state, then carry the generated judgment into an executor handoff that inspects filesystem-backed resident artifacts and writes a bounded evidence artifact under resident handoff evidence.",
      "status": "open",
      "evidence_refs": [
        "resident/continuation/momentum/runs/proof/reflections/reflection-proof.md"
      ],
      "source_refs": [
        "resident/continuation/momentum/state/patches/patch-proof.md"
      ],
      "updated_at": "2026-06-28T10:00:00Z"
    }
  ],
  "stale_assumptions": [],
  "recent_lessons": [
    "Current-state handoff is only useful if future attention can see and apply it, then hand a generated brief to an executor that can inspect resident artifacts in its configured workspace and create requested evidence inside that workspace."
  ],
  "candidate_reflexes": [],
  "candidate_capability_gaps": [],
  "source_refs": [
    "resident/continuation/momentum/state/patches/patch-proof.md"
  ],
  "compaction": {},
  "updated_at": "2026-06-28T10:00:00Z"
}
```
