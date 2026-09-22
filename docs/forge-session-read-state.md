# Forge session inbox state — local implementation

September 20, 2026. User-authorized paired Forge/iOS implementation only: no deployment,
service restart, remote push or release. This branch is not the running gateway.

## Contract

Read/unread is a per-authenticated-reader projection, **not** a replacement session
status or activity state. Session list/detail responses add nullable `read_state`.
One bulk query decorates a list; no transcript hydration or per-row fetch is needed.

`GET /api/v1/forge/sessions/{id}/read-state` returns:

```json
{
  "latest_output_seq": 42,
  "latest_output_turn_id": "stable-final-turn-id",
  "latest_output_at": "2026-09-20T12:00:00Z",
  "read_through_seq": 30,
  "manually_unread": false,
  "revision": 2,
  "is_unread": true
}
```

`PATCH` on the same route accepts:

```json
{"state":"read","through_seq":42,"expected_revision":2}
```

Use `state: "unread"` to leave a reminder. There is no caller-selected reader ID;
existing identity and session authorization apply. The Niuu aggregate forwards both
methods to the owning instance using the existing authenticated proxy path.

- A read advances only through the final cursor actually observed by the client.
  A newer final arriving concurrently remains unread.
- Reader actions use compare-and-swap revisions. A stale automatic read cannot
  clear a newer manual unread action: HTTP 409 means refresh, not blind retry.
- Future cursors / invalid bodies return 422, missing sessions 404, absent reader
  identity 401 and denied session access 403.
- Reading does not modify lifecycle, runtime activity, last-active time or history.
- `is_unread = manually_unread || latest_output_seq > read_through_seq`.

## Persistence and completion

Migration `000075_session_read_state` adds the latest-final projection to `sessions`
and a `(session_id, user_id)` reader table. Up/down SQL is mirrored in the Helm
migration ConfigMap inside its enabled guard. Session deletion cascades markers.

The shared broker/replay result reducer stamps `metadata.final_output: true` on
successful result completion. Only a newly inserted public assistant
`conversation.turn` with that marker, a stable ID and nonempty text updates the
projection. Tool ticks, partial output, errors, interruption and internal messages
do not generate a successful-final unread signal. A subsequent new successful
final does; session runtime may already be running again without hiding unread.

The final projection and durable event append commit together. Exact retries and
conflicting reused sequence numbers cannot resurrect an already-read output.
Explicit historical imports use their separate insert path and do not manufacture
new inbox deliveries. Migration starts existing sessions at a zero cursor; it does
not sweep historical sessions into unread. Updated brokers are required for new
final markers; migration/backend code alone cannot upgrade already-loaded writers.

The session row serializes final projection updates with reader mutations. The release integration preserves the existing append/import session-row lock for
all frames; the final projection adds no second lock. `session_read_state`
SSE events are refresh hints carrying only session/owner IDs, never private reader
markers. Hint failure does not fail an already-committed mutation; ordinary relisting
recovers missed hints. iOS fetches the affected small projection, not the whole fleet.

## Validation and remaining gates

- **773 focused tests passed**, including broker/reducer replay parity, REST,
  authenticated aggregate proxy, reader isolation, cursor/revision races, SQL adapter,
  log conflicts, chart and migration parity.
- New pure inbox domain module: **100% statement/branch coverage**. This is focused
  coverage, not a full-repository 85% coverage-gate claim. No gate was lowered.
- Ruff and whitespace checks passed. Python tests used the existing host Python
  3.11.15 environment; supported-target Python 3.12+ CI remains a separate gate.
- Real PostgreSQL integration test is added under
  `tests/integration/volundr/test_session_read_state.py`, **not run locally**. No DB
  container was started, no live DB was altered and no migration was applied.
  Real SQL transaction/concurrency behavior requires the established CI test DB.
- Paired iOS native typechecking/rendering, real cross-device SSE synchronization,
  loss/reconnect and manual UI acceptance remain pending. No rollout is implied.

Local evidence:
`/home/thor/repos/worktrees/niuu-lexi-session-unread-20260920/.local/unread-state-20260920/`
(`regression-final.log`, `read-state-coverage-final.json`, `lint-final.log`).
Paired iOS worktree:
`/home/thor/repos/worktrees/lexi-ios-chat-voice-ux-20260916`.

Worker `thor:c8103dc7-a834-5e32-a06d-1a98d7a8e2b5`; parent
`thor:8f20102d-6da7-58aa-98c2-e0bce2deab97`. This is a worker implementation record,
not user acceptance. Prior session-list latency/ownership review and archive-cleanup
questions remain separate and unchanged.
