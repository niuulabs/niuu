# NIU-1147 closure

- **Tracker identifier:** `NIU-1147` (`388ce0db-8f0d-49bb-9e2b-345c7c7bd6ef`)
- **Merged branch:** `regin`
- **Target branch:** `regin`
- **Merge commit SHA:** `7c07ad90fe6c376760e6f950e9384d515d93da95` (direct-to-target delivery; no separate hosted PR)
- **Final tracker status:** `Done`

## Operator summary

Tool & Skill Builder now routes a legitimate publisher `capability.blocked` result to the explicit `capability-blocked` input-required gate while retaining its causal Mímir workload-identity diagnosis, without claiming a publication. The existing `capability.ready` success route remains intact. Focused execution and system-workflow tests passed (26 passed), and focused Ruff validation passed; the only remaining bundled-workflow lock failure is the independently verified unrelated Tracker Delivery Flow mismatch.
