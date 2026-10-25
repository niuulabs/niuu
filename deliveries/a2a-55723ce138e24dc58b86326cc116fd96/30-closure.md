# Closure — NIU-1150

- **Tracker identifier:** `NIU-1150`
- **Merged branch:** `regin` (direct integration; source and target are both `regin`)
- **Target branch:** `regin`
- **Merge commit SHA:** `662a7f5d35750eae29a8fa1ab99f1d0640a6e68c` (final code integration commit; no synthetic Git merge commit was needed)
- **Final tracker status:** `Done`

## Operator summary

Ravn now resolves visible Agent Card URL aliases to canonical Guild agent IDs before persisting durable A2A task handles. Those handles are directly usable by `a2a_task.get`; legacy URL handles remain readable only while visible, and canonical aliases share the active-task duplicate guard without broadening Guild visibility.

The reviewed `regin` delivery was already directly integrated and pushed. Focused pytest (20 passed), Ruff, and diff checks passed in review; no hosted PR or CI run applies to this direct-integration delivery.
