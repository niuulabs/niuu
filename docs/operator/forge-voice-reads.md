# Forge reads for voice (LEX-1677)

A voice assistant asks Forge short questions, such as "what did the session last
say?" or "what are my sessions?", and needs short answers fast. These reads are built
for that. The default reads are unchanged, so the apps are not affected.

## Capabilities

`GET /api/v1/forge/feature-flags` → `capabilities`:

| Flag | Meaning |
|---|---|
| `voice_conversation` | `GET /sessions/{id}/conversation?detail=voice` is served |
| `summary_sessions` | `GET /sessions` honours `limit` and `fields=summary` |

On a Guild host, each flag is `true` only when the node behind the facade reports it.
A client must check a flag before using the read. An older Forge treats
`detail=voice` as a full read and ignores `limit` on the list.

## The last messages of a session

```http
GET /api/v1/forge/sessions/{id}/conversation?detail=voice&limit=5
GET /api/v1/forge/sessions/{id}/conversation?detail=voice&limit=5&since_turn=<turn_id>
```

```json
{"session_id": "…", "detail": "voice", "limit": 5, "activity_state": "idle",
 "total_turns": 412,
 "messages": [
   {"role": "user", "text": "Is the deploy done?", "created_at": "…", "turn_id": "…"},
   {"role": "agent", "text": "Yes, both hosts are live.", "created_at": "…", "turn_id": "…"}
 ]}
```

**How it reads:**

- It reads the newest `limit` turns from the end of the source. A running session's
  broker builds only that page. A stopped session reads only the tail of its
  durable log. This is the same path as `tail=N`.
- It keeps user and agent turns that carry text, and drops everything else: tool
  calls, tool results, attachments, system and internal turns, and turn metadata.
- A turn that is only tool calls has no text, so it is dropped. You can therefore get
  fewer than `limit` messages.
- A reply still streaming carries `in_progress: true`.
- `total_turns` is present only when the source counted the whole transcript.

**Parameters:**

- `limit` is required. It must be between 1 and `conversation_recent_max_turns`, the
  broker's recent-page bound. Any other value gets 422.
- `detail=voice` takes only `limit` and `since_turn`. Every paging parameter
  (`tail`, `cursor`, `before`, `after`, `after_id`, `turn_id`, `history_protocol`,
  `max_bytes`) gets 400.
- With `since_turn`, only messages after that turn are returned. The response also
  carries `since_turn_found`:
  - `true`: the turn was inside the page, so nothing new is missing. An empty list
    means "nothing new".
  - `false`: the turn is older than the page. Every message returned is newer, but
    more may lie before the page. Re-read with a larger `limit`, or open the
    transcript.

**Size:** the text is returned whole, never truncated. A long agent reply makes a
larger page. Ask for a smaller `limit` when that matters.

**Measured on Thor:**

- `tail=1&detail=shallow` (the same read path) took 35–130 ms on a large live
  session.
- `detail=shallow&limit=1` took about 0.5 s, because it builds the whole transcript
  before slicing.

## The session list

```http
GET /api/v1/forge/sessions?limit=10
GET /api/v1/forge/sessions?fields=summary&limit=10
```

- **`limit`:** returns the N sessions with the newest activity. Without it, every
  session is returned in the existing order and shape.
- **`fields=summary`:** returns, newest activity first:
  `{id, name, status, activity_state, short_description, project: {id, role} | null,
  last_activity_at, worked_seconds_24h, unread, node}`.
  - `node` is the Forge host key (`thor`, `mini-m6`).
  - The Guild facade also adds `instance_id`, `instance_name` and `instance_slug`.
  - `unread` is null for a session you only participate in.
- **Through the Guild:** each node returns at most `limit` rows, and the facade
  keeps the newest `limit` across nodes.

## Worked time (LEX-1465)

Every row of `GET /sessions`, full or summary, carries `worked_seconds_24h`: the seconds
Forge spent on that session in the last 24 hours. That supports a line like "Today · 6
sessions · 2 h 14 m": count the rows with a value above 0, and sum the values.

It is the union of the session's assistant turns (`turn.assistant` spans), clipped to
the window, so concurrent turns, such as teammates', count once. A turn whose end was
never recorded is bounded by evidence:

- the latest one counts until now while its session is running and not idle, and
  otherwise until the session's `last_active`;
- an older one counts until the last tool call recorded inside it, never past the next
  turn's start, and counts nothing when there is no such call.

It is computed in one query per list, about 90 ms for 65 sessions on Thor. It is null
only on a Forge that records no turn spans.

## Timing

`X-Forge-Read-Ms` carries the server's own time for the read, in milliseconds. It is
set on `detail=voice` conversation reads and on `GET /sessions`. On a Guild host the
facade sets it to its total time, including the node's. The client subtracts it from
its round trip to separate Forge time from network and service time.
