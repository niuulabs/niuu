# Implementation — NIU-1134

## Change

Added a **Live progress and persisted judgments** section to the Resident HUD
documentation. It explains that in-flight task telemetry is ephemeral and
shows `Not emitted yet` until a turn completes; completed turns supply the
authoritative persisted outcome and working-state snapshot. It also documents
the restoration and `LIVE` / `STALE` lifecycles.

## Verification

Verified the wording against `src/ravn/static/resident-hud.html`:

- Active tasks render `Not emitted yet` for the current judgment.
- Completed task judgments are read from a matching persisted turn.
- Restored durable tasks do not retain detailed live activity unless a trace is
  available.
- `LIVE` and `STALE` reflect polling freshness.

`git diff --check` validates the documentation-only diff.

## Delivery

- **Branch:** `regin`
- **Base:** `origin/regin`
- **Hosted PR:** Not created; the workspace has no GitHub CLI available. The
  committed revision is pushed to `origin/regin`.
