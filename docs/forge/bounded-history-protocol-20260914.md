# Bounded conversation history — protocol 2

Status: isolated server candidate; **not deployed**. Thor/Forge/network HOLD
remains. No gateway or owner session was restarted, adopted, or sent test input.
Native integration belongs to the existing iOS owner, not this source checkout.

## What broke

A message-only client was inadvertently requesting a conversation replay. Forge's
`POST /sessions/{id}/messages` bridge connected to the session WebSocket without
history negotiation. The gateway therefore tried its legacy *complete* snapshot,
not the recent window already requested by shipped iOS build 2264.

Read-only inspection of the affected gateway found a 10.7 MB saved conversation.
Even the shallow complete wire snapshot was 1,032,133 bytes, above the unchanged
921,600-byte cap. A recent window fit in 222,082 bytes. Both successful recent
connections and failed full-history connections appear in the gateway log. Two
failed full-history connections line up with saved coordinator message requests;
the bridge's default and isolated tests establish the defective ingress behavior.
No live message probe was needed.

The warning was sent only to the connecting socket, **not broadcast directly to
all viewers**. But the per-connect error was also durably logged. The raw-history
reducer treated it as an agent error: it flushed assistant work, inserted warning
prose as an assistant row, and marked the transcript partial. A saved parent REST
response actually contains that row and `_prep.fault_c_rebuild_ms`; a later live
response does not. This explains an intermittent warning visible through polling.
It does not establish that the normal saved conversation file was modified.

The fix classifies the exact legacy error *code* and new typed history controls
before any conversational reduction. It does not match or suppress message text.
The original raw ledger remains retained and genuine agent errors remain visible.

## Negotiation and compatibility

New viewers use both `history=recent` and `history_protocol=2` on `/session`.
The old proxy already forwards `history=recent`, so it remains useful when the
new parameter is ignored. New gateway capabilities advertise `history_protocol:2`.
The aggregate facade preserves typed history errors and does not append timing
fields after the owner has fitted a protocol-2 page to its byte budget. It also
forwards explicit full-item reads to the authorized owner.

Clients must also inspect the actual history response, not assume a query was
honored. Unknown response protocols do not authorize using an opaque cursor.

Message-only ingress additionally sends `history_delivery=none`. A new gateway
skips history for that connection; a retained old proxy/gateway still uses the
bounded recent path. Request identity and delivered/pending/failed ACK semantics
are unchanged. Nothing resends a user message.

| Running components | Available behavior |
| --- | --- |
| Old Thor API + retained gateway | Existing recent WS and bounded outward REST `limit`/`max_bytes`; client-only recovery fixes can help now. No new cursor/control contract. |
| New API + retained gateway | New API validates/pages old full responses and excludes raw recovery controls when rebuilding. Outward payloads are bounded; old upstream serialization still is not. |
| New API + new gateway | Same cursor contract at source; selected rows are serialized/elided, not a deep copy of every historical tool output. Coherent WS snapshot/live boundary and typed gap controls. |
| Archived session | Same presentation cursor/page contract over the archive projection. This is not a database-native turn index. |

Legacy full-history WebSocket clients retain their complete-snapshot contract
and size cap. Oversize delivery remains an explicit per-connect legacy error,
never silent history truncation. New behavior does not justify restarting old
sessions to retrofit their loaded code.

## REST page contract

```
GET /api/v1/forge/sessions/{id}/conversation
    ?history_protocol=2&detail=shallow&limit=15&max_bytes=262144
    [&cursor=<opaque token>]
```

The equivalent gateway route is `/api/conversation/history`. For protocol 2,
count and byte limits default to configured recent limits and are capped by them.
Requesting `detail=full` cannot evade bounded page elision. Legacy `before`,
`after`, `after_id`, and explicit item lookup cannot be mixed with protocol 2.

The response retains `turns`, activity fields and `projection_revision`, and adds:

- `history_protocol: 2` and `history_source`: `gateway`, `legacy_gateway`, or
  `archive` (the last includes a durable-log-derived transcript).
- `total_turns`: rows in the served projection, before page elision.
- `window_offset`, `window_end`: actual absolute half-open interval returned.
- `requested_window_offset`, `requested_window_end`: interval requested before
  byte/count fitting; `page_kind`: `recent`, `older`, or `refresh`.
- `has_more_before`, `older_cursor`: seek strictly before the first returned row.
- `refresh_cursor`: re-read precisely the **returned** fixed interval, allowing
  late text/tool/status changes without growing the requested span.
- `page_complete`: whether all rows of the requested interval were returned.
- `continuation_cursor`: for an incomplete **refresh**, the exact still-unread
  prefix of that same interval. It is null once no continuation remains.
- `head_seq`: gateway event-log observation head, or null when unavailable.
  **This is not a contiguous public-message counter or a raw resume cursor.**
  The ledger includes private/per-connect/non-broadcast entries.

A growing refresh keeps a contiguous newest suffix. Its continuation covers
`[requested_window_offset, window_offset)`, not an unbounded or moving tail.
A client must finish every continuation before marking the original interval
refreshed. Initial recent fitting does not require eagerly loading omitted history;
scrolling uses `older_cursor`. `page_complete` describes row coverage, not whether
every item's heavy details are inline.

Tokens bind the session, projection revision and ordered prefix identities.
Ordinary appends and content/status/tool updates do not move the seam. A repair,
resegmentation, absent seam, or wrong session returns:

```
HTTP 409
{"detail":{"code":"history_cursor_invalid","recovery":"recent","history_protocol":2}}
```

Malformed cursors return 400. Tokens are opaque seek handles, **not credentials**;
the route delegates authorization to the existing session authorization adapter
before any history access. Never treat an ignored token or an invalid empty window
as a successful page. Never infer authority from token contents.

## Huge single item and lazy details

A huge tool output normally retains its call and a lazy result placeholder, using
the existing `/sessions/{id}/tool-result/{tool_use_id}` route.

If one whole row cannot fit, it is explicitly `history_preview:true`, with a
row-level `history_ref:{"turn_id":"<stable ID>"}` and `preview_omitted_parts`.
The item preview does not pretend that a partial tool group is complete. Even
oversized metadata is handled with a bounded identity-only preview. Originals
are not mutated. Explicit detail opening uses:

```
GET /api/v1/forge/sessions/{id}/conversation/turns/{turn_id}
{"turn":{...full row...},"projection_revision":"..."}
```

This user-requested expansion is intentionally full, not an automatic recovery
or polling path. A missing/resegmented item returns 404. Preview data must never
erase an already expanded full item in native cache state. Historical question
text is not permission to answer a current runtime RPC.

## WebSocket connection and recovery

Protocol 2 sends one bounded `conversation_history` frame carrying the same page
metadata/cursors, then live frames. Reconnect requests a new bounded recent
snapshot, not the raw conversation ledger.

The CLI path can broadcast an event before its transcript reduction, with awaits
between. Snapshot readers now wait for observed in-flight mutation work to finish;
writers are neither locked nor serialized by this mechanism. Capture is synchronous,
and the channel is registered only after capture. Live events during awaited
snapshot delivery are buffered behind it, bounded by count and bytes. Overflow is
explicit recovery, not silent partial success. Cancellation of a read does not
cancel a writer. Pending control replay remains separate from transcript rows.

```
{"type":"history_gap","history_protocol":2,
 "reason":"snapshot_too_large|snapshot_race|live_frame_too_large",
 "recovery":"recent","head_seq":null,"projection_revision":null,
 "_per_connect_handshake":true}
```

The reason above denotes alternatives, not one literal combined value. Controls
must not create assistant rows or close a turn. Keep supporting the exact legacy
`error/code=conversation_history_too_large` form too. If no coherent REST snapshot
is available within the configured read wait, it returns 503 with
`detail.code=history_busy`, `recovery=retry`; direct gateway responses include
`Retry-After:1`. This is a bounded failed read, not an empty successful history.

Native owns request-generation/revision fences, coalesced automatic recovery,
cancellation, ID upserts, bounded refresh continuations, and preservation of the
local input journal. Busy/failed recovery must terminate in a readable retry state;
a stale or cancelled request must not overwrite a newer generation. No input resend.

## Limits and remaining scope

- Config defaults remain recent 15 rows / 256 KiB; the legacy WS ceiling is not
  raised. Bootstrap defaults to 256 frames and the configured live-frame byte cap.
  Reader wait defaults to 2 seconds. Configuration is in the package settings and
  chart YAML, not new production environment edits.
- The new gateway still walks identity/timeline metadata across its in-memory
  history. It avoids deep-copying and JSON-serializing unselected heavy payloads;
  it does not make all projection CPU work independent of history length.
- A retained gateway's full upstream response still costs memory and time at the
  API. Outward pagination cannot change already-running old code.
- Archive/file reads still load a stored JSON projection. Database-only recovery
  still uses the existing paged frame read + reducer, whose default ceiling is the
  newest 50,000 frames. Its totals can therefore describe an incomplete historical
  projection. A persistent indexed turn projection/complete older backfill is a
  separate unresolved storage limitation, **not claimed fixed by this transport
  page contract**. No raw data or database schema was changed here.
- No native runtime, physical-device, live-provider, or deployment acceptance is
  implied by isolated server tests. The native owner must validate its side of the
  contract; the coordinator separately reviews any future maintenance proposal.
