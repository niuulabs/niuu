# Recent Forge activity and on-demand history

Live clients should open at the latest activity and keep receiving events while older history stays in durable storage. A large transcript must not force users out of the live session.

## Wire contract

- ForgeKit negotiates `history=recent` on the advertised WebSocket URL. Existing authority, path, auth headers, and percent-encoded query values remain intact.
- Opted-in brokers send at most `conversation_recent_max_turns` (default 15) and `conversation_recent_max_bytes` (default 262144), also respecting the existing socket ceiling. `total_turns`, absolute `window_offset`, `head_seq`, and projection revision retain their meaning. An in-progress trailing turn counts as one turn.
- Legacy clients continue receiving the complete-snapshot contract. Existing brokers can keep running during deployment; the new app handles their `conversation_history_too_large` response by fetching recent REST activity automatically.
- REST accepts optional `max_bytes` alongside `limit`, `before`, and `after`. Default zero preserves complete requested pages. Seam mismatch still returns empty turns with `window_offset=-1`.
- The projection elides tool payloads using existing lazy references, then removes oldest whole turns until it fits. An exceptionally large single turn is an explicit `history_preview`: newest text/parts, stable identity and activity metadata. Omitted tools are never fabricated as successful results or partial actionable questions. Full persisted content is untouched.
- The app asks for bounded activity on initial load and automatic refresh. It skips oversized disk caches, does not persist previews as complete history, and keeps already-visible rows across contiguous refreshes. Older-page and preview expansion are explicit requests; an expanded span remains visible during subsequent refreshes.

## Acceptance tests

1. Reconnect to a long running session: newest tool/text sequence appears, older count is accurate, no large-history warning, live events continue.
2. Test 100+ turns and a single enormous active turn, including multibyte and JSON-escaped text. Measure actual serialized bytes, retain cursor/absolute offsets, and compare source objects before/after.
3. Expand older history and a preview. Confirm complete stored text, correct page boundaries, and uninterrupted active state, questions, and tools.
4. Reconnect through a predeployment broker: recent REST recovery works without restarting the native process.
5. Repair the durable projection or mismatch an incremental seam: reject the stale cache and refetch a recent canonical window.
6. Run legacy complete-snapshot, database fallback, event log, and crash/reconnect regression tests.
7. On iOS simulator, verify recent replay and older-history UI; do not use a physical phone for this change.

Focused backend command:

```sh
python -m pytest tests/test_skuld/test_conversation_snapshot.py \
  tests/test_volundr/test_conversation_db_fallback.py \
  tests/test_skuld/test_event_log.py tests/test_skuld/test_forge_crash_reconnect.py \
  --cov=skuld.conversation_snapshot --cov-report=term-missing
```

The aggregate API still compares broker history with durable history before paging. This preserves the existing recovery decision; the new budgets bound the response delivered to clients, not the durable record or every internal broker-to-aggregate read.
