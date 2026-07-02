# Momentum current-state proof signal 1

We are implementing NIU-1076. Momentum now has historical artifacts, but future
extract runs still need a compact current resident understanding.

The important tension: if a reflection teaches Niuu that context dilution is
the active failure mode, the next extraction must receive that consolidated
state deterministically instead of relying on fuzzy recall.

Constraints:

- Use normal `ravn momentum extract` and `ravn momentum reflect`.
- Persist through selected resident state.
- Keep provider details inside adapter config.
- Candidate reflexes and capability gaps remain candidate-only.
- Do not add dogfood, eval, proof, scheduler, daemon, autonomy, or wrapper lanes.
