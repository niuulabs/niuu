# Native session recovery and durable history

Recovery has two independently verified outcomes: the CLI resumes the requested
native thread, and Forge's persistent event log contains the recoverable public
conversation. A successful native resume alone is insufficient for iOS.

## Import workflow

1. Discover an existing Claude Code or Codex session through
   `GET /api/v1/forge/external-sessions`, or supply its known native ID.
2. `POST /api/v1/forge/sessions/import` with `provider`, `external_id`, and an
   optional `name`. The provider reads and validates the native transcript before
   a new Forge row is created. Workspace policy still applies.
3. Before returning 201, persist the normalized history and completion marker in
   one PostgreSQL transaction. Native timestamps and source provenance survive.
4. Inspect `GET /sessions/{id}/conversation` while the session is still stopped.
   Normal user and assistant turns, tool inputs, and available outputs should be
   present. Inspect `/log/head` and replay independently.
5. Resume the session. Broker startup loads the durable prefix into its local
   conversation cache before starting the CLI. Confirm `cli_session_id` equals
   the requested `external_session_id`.
6. Connect iOS, inspect earlier messages and tool results, then send a follow-up.
   The imported prefix must remain visible in REST and reconnect snapshots after
   that new turn completes.

The import never executes a recorded command, edits a recorded file, or asks the
model to recreate missing history. Native context is restored by the CLI's own
resume protocol; display history is reconstructed from observed native records.

## Repair an older empty recovery

Stop the specific recovered Forge session, then call:

```http
POST /api/v1/forge/sessions/{id}/history/import
```

The route uses its stored provider/native ID and verifies authorization and
workspace identity before reading the transcript. It accepts only a created,
stopped, or failed target with no captured conversation. Init, capabilities, and
connection welcome rows are retained at their existing sequence numbers.
Imported frames append after that prefix, followed by a non-broadcast marker.
No existing rows are deleted, reordered, or overwritten.

The result reports `imported_frames`, `source_frames`, and `partial`. Identical
snapshot retries are idempotent. Different source identities or changed snapshots
conflict rather than claiming newly available information was imported. Existing
meaningful conversation data also conflicts; merging two partial conversations
requires a separate reconciliation design.

If persistence fails after creating a new Forge session, the error identifies
that session and its retry endpoint. The history transaction rolls back in full.
An unreadable, malformed, or empty native transcript fails before creating a
session, avoiding an apparently successful empty recovery.

## Capture contract

| Native source | Imported representation | Deliberate limits |
| --- | --- | --- |
| Codex modern completed items | User/assistant text, command execution, file changes, supported tool/search items | Private reasoning is excluded |
| Codex legacy response items | Public messages, function/custom tool calls and outputs | Mirrored messages and identical tool IDs are deduplicated |
| Codex task completion | Final result boundary and available final text | Interrupted work retains its recorded cancellation |
| Claude Code primary transcript | Public user/assistant text, tool use and results | Sidechains, injected context, signatures and private thinking are excluded |
| Partial final JSONL write | All complete preceding records, with partial provenance | Invalid interior JSON fails; no invented completion |

Each imported frame records provider, external ID, source line/type, source
snapshot hash, native item ID when available, and partial status in
`metadata.native_import`. Outer orchestration calls and the concrete commands they
launch have separate native IDs and remain distinct observed activities.

The parser takes a bounded native snapshot (64 MiB default, configurable through
provider `max_transcript_bytes`). Broker hydration has separate bounds under
`SKULD__HISTORY_HYDRATION_*`: enabled, timeout seconds (30), raw page size (1000),
maximum frames (100000), and maximum bytes (64 MiB). Hydration follows a frozen
log head. Public filtering happens after raw paging, so a short or empty visible
page does not mean end of transcript.

If hydration cannot establish a complete, consistent prefix, it preserves the
existing cache and logs the reason. It never silently replaces richer local
history with a partial network response. This remains an operational failure to
investigate; a broker starting is not proof that history hydration succeeded.

Large reconnect snapshots use the existing shallow tool-result format to stay
below iOS's current WebSocket receive limit. All conversation text and tool IDs
remain present; complete tool outputs are fetched on demand from persistent
history. The default socket budget is 900 KiB, configurable with
`SKULD__CONVERSATION_SNAPSHOT_MAX_BYTES`. A transcript that still exceeds the
budget produces an explicit error instead of a truncated authoritative snapshot.

## Automated checks

- Provider fixtures: identity validation, repeated messages, mirrored native
  representations, tool/result pairing, error output, source timestamps,
  sanitization, truncated final records, malformed interior records, and size
  bounds. Fixtures contain synthetic content, never private user traces.
- Service and HTTP contracts: automatic persistence before success; no empty row
  on parse failure; recoverable commit failure; owner authorization before file
  access; workspace drift; missing native identity; explicit 401/403/404/409/422/
  503 responses; facade owner routing and authentication forwarding.
- PostgreSQL integration: import/marker atomicity, rollback on marker failure,
  retry behavior, snapshot conflicts, NUL-safe payloads, sequence preservation,
  target lifecycle guards, and coordination with concurrent live append.
  These tests create and remove isolated schemas.
- Broker contracts: missing local cache, complete frozen reads, filtered page
  gaps, byte/frame/time limits, malformed responses, richer local data, unsaved
  tails, unchanged cache on failure, and no duplicate event-log writes.
- Client surfaces: real test WebSocket reconnect snapshot and REST history retain
  the imported prefix after an additional live user/assistant turn.
- Cold reads retain the imported prefix even after later live canonical turns
  have been persisted. An unfinished imported assistant remains interrupted;
  a later live completion cannot falsely finish the old work.
- Socket size checks measure the actual JSON encoding, including non-ASCII
  escaping. Large tool outputs become recoverable lazy references without
  changing the database or local full-history cache.

Run the normal Forge unit gate with `scripts/verify_forge.py unit --coverage`.
The PostgreSQL lane also includes `test_pg_history_import.py`; set
`FORGE_HISTORY_TEST_DATABASE_URL` to an authorized test database. CI supplies its
service database and treats skipped required database tests as a gate failure.
Do not lower the repository's 85% coverage threshold to make a run green.

## Live acceptance record

For each live repair, retain private before/after evidence: native snapshot hash,
Forge/native IDs, initial raw head, imported frame count, completion marker,
conversation counts, public replay equality, reconnect snapshot equality, and
broker thread-resume identity. Confirm unrelated active session processes were
preserved during platform maintenance. Do not commit native logs or credentials.

The Lexi Astra repair is recorded in
[the incident report](forge-lexi-astra-incident-2026-09-08.md).
