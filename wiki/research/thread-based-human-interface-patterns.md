---
type: research
confidence: high
produced_by_thread: true
related_entities: []
source_ids: [src_niu777_telegram_bot_api, src_niu777_slack_api, src_niu777_discord_api, src_niu777_teams_bot_fw, src_niu777_matrix_spec]
---

# Thread-Based Human Interface Patterns for Active Sessions

> **TL;DR** — Map each agent session to exactly one platform thread using a generic `CommunicationRoute` envelope that carries platform, conversation ID, thread ID, and direction metadata. Mirror public-facing agent output and human-approval prompts; keep internal tool calls, thinking blocks, and inter-agent mesh traffic internal.

## Compiled Truth

### Recommended Generic Routing Model

The routing layer sits between the session broker and platform-specific channel adapters. Every active session maintains zero or more `CommunicationRoute` records that describe where human-facing messages should be sent and where inbound replies should be dispatched.

**Route lifecycle:**

1. **Bind** — When a session starts with a channel configured, the adapter resolves or creates a platform thread and registers a `CommunicationRoute` on the broker.
2. **Dispatch outbound** — The broker filters events through a mirror policy (see below), formats surviving events via the adapter's formatter, and sends them to the thread identified by the route.
3. **Dispatch inbound** — The adapter receives platform events (webhook, polling, or gateway), matches them to a session via the route's thread identifier, validates the sender, and injects the message into the session's input stream.
4. **Unbind** — When the session stops, the route is deregistered. The platform thread may be closed, archived, or left open depending on adapter policy.

**Route envelope (generic):**

```
CommunicationRoute {
  platform:        string        // "telegram" | "slack" | "discord" | "teams" | "matrix"
  conversation_id: string        // chat/channel/room — the parent container
  thread_id:       string | null // platform-specific thread identifier
  direction:       "bidirectional" | "outbound_only"
  session_id:      string        // the Volundr session this route is bound to
  metadata: {
    topic_mode:    string        // how the thread was created
    topic_name:    string | null // display name if created by the adapter
    notify_only:   bool          // true = suppress inbound handling
    created_by:    string        // "adapter" | "user" | "pre-existing"
  }
}
```

This aligns with the existing `TelegramChannel.communication_route()` return shape in `src/skuld/channels.py` and generalises it for multi-platform use.

**Inbound routing algorithm:**

1. Extract `(platform, conversation_id, thread_id)` from the inbound event.
2. Look up the matching `CommunicationRoute` in the broker's route table.
3. If no match, drop the message (or queue it for a configurable grace period if a session is starting).
4. Validate the sender against the session's ACL (owner, allowed operators).
5. Inject the message as a `user_confirmed` event with `metadata.source_platform` set — this prevents echo loops where mirrored output is re-ingested.

### Platform Thread Identifier Reference

| Platform | Thread ID field | Type | Create method | Inbound delivery |
|---|---|---|---|---|
| Telegram | `message_thread_id` | Integer | `createForumTopic` | Webhook / `getUpdates` — filter by `message_thread_id` |
| Slack | `thread_ts` | String | `chat.postMessage` with `thread_ts` of parent | Events API `message` event with `thread_ts` present |
| Discord | Channel `id` | Snowflake (String) | `POST /channels/{id}/threads` | Gateway `MESSAGE_CREATE` in thread channel |
| Teams | `conversation.id` (contains `messageid=`) | String | `CreateConversationAsync` / Bot Framework | Bot Framework webhook Activity |
| Matrix | `event_id` of root | String | Send event with `m.relates_to.rel_type: "m.thread"` | `/sync` or appservice transaction — filter `m.relates_to` |

### Per-Platform Notes

**Telegram**
- Forum topics require a **supergroup** with forum mode enabled and bot **admin rights**.
- The General topic has a fixed `message_thread_id = 1` and cannot be deleted.
- Topic name max: 128 characters. Truncate session names before using as topic names.
- Rate limit: ~30 messages/second globally, ~1 message/second per chat. Buffer and batch outbound messages.
- Three topic modes apply: `shared_chat` (no threading), `fixed_topic` (pre-configured thread), `topic_per_session` (adapter creates a topic on session start). The existing `TelegramChannel` already implements all three.
- Closing a topic via `closeForumTopic` signals session end without deleting history — preferred for archival.

**Slack**
- Threads are implicit: post a message, then reply to its `ts`. No explicit "create thread" API.
- Recommended pattern: post a session-start message to the channel, capture its `ts`, use it as `thread_ts` for all subsequent messages.
- `reply_broadcast: true` can surface key messages (session start, final outcome) in the main channel timeline, but use sparingly — it creates noise.
- Thread replies arrive as `message` events with `thread_ts` present. Filter: `thread_ts != ts` means it is a reply.
- New commercial non-Marketplace apps face aggressive rate limits on `conversations.replies` (1 request/minute, 15 results/page as of 2025). Prefer event-driven inbound over polling.
- No thread-specific subscription filter — the app receives all channel events and must route by `thread_ts`.

**Discord**
- Threads are first-class Channel objects (types 10, 11, 12). Public and private thread types exist.
- Auto-archive after inactivity: 60 min, 24 h, 3 days, or 7 days. Set `auto_archive_duration` to 10080 (7 days) for sessions and extend on activity.
- Thread members must be explicitly added or must post to join. Bots need `SEND_MESSAGES_IN_THREADS` permission.
- Messages arrive via standard `MESSAGE_CREATE` gateway event — the `channel_id` is the thread ID.
- Global rate limit: 50 requests/second per bot. Per-route limits returned in response headers.
- Forum channels are a natural fit for topic-per-session: each session becomes a forum post with its own thread.

**Microsoft Teams**
- Thread identity is embedded in `conversation.id` as a `messageid=` suffix — extract this, not `replyToId` (which is unreliable for inbound activities).
- Bots only receive messages when **@mentioned** unless the app has RSC `ChannelMessage.Read.Group` permission.
- Proactive messaging requires a stored `ConversationReference` with valid `serviceUrl`, `conversationId`, and `tenantId`.
- Rate limit: ~4 messages/second per conversation.
- No native "close thread" concept. Session end must be signalled by a final message rather than thread state change.

**Matrix**
- Threads use the `m.thread` relation type (stable since spec v1.4, September 2022).
- Nested threads are explicitly forbidden — the root must be a non-thread event.
- Thread root events include aggregated metadata (`unsigned.m.relations.m.thread.count`, `latest_event`).
- Application services are typically exempt from rate limiting, making Matrix well-suited for high-throughput mirroring.
- Fetch thread history via `GET /rooms/{roomId}/relations/{eventId}/m.thread` (paginated).

### Required Route Metadata

The minimum metadata a channel adapter must persist or provide to enable reliable routing:

| Field | Purpose | Where stored |
|---|---|---|
| `platform` | Adapter selection | Route record |
| `conversation_id` | Parent chat/channel/room for API calls | Route record |
| `thread_id` | Thread-level targeting for send and receive | Route record |
| `session_id` | Maps inbound messages to the correct session | Route record |
| `topic_mode` | Controls thread lifecycle (create, reuse, shared) | Route metadata |
| `created_by` | Distinguishes adapter-created threads from user-provided ones — affects cleanup policy | Route metadata |
| `notify_only` | Suppresses inbound dispatch for observe-only channels | Route metadata |
| `source_platform` | Stamped on injected inbound messages — prevents echo loops | Event metadata |

### Mirror Policy: What To Surface vs. Keep Internal

**Mirror outbound (send to human thread):**

| Event type | Rationale |
|---|---|
| `room_message` (visibility=public) | Agent's public-facing output — the primary human interface |
| `room_notification` | Alerts requiring human attention (blocked, needs approval) |
| `room_outcome` | Session results, verdicts, summaries |
| `user_confirmed` (from browser) | Echo user prompts so the thread reads as a conversation |
| `error` | Critical failures the operator should see |
| Permission requests | Approval/deny prompts via inline controls (buttons, reactions) |

**Keep internal (do not mirror):**

| Event type | Rationale |
|---|---|
| `content_block_delta` (thinking) | Internal reasoning — noisy, often large, and exposes chain-of-thought |
| `assistant` tool_use blocks | Tool invocations are implementation detail — surface only via formatted summaries if desired |
| `room_message` (visibility=internal) | Inter-agent coordination, not for human consumption |
| `room_mesh_message` | Delegation/orchestration traffic between agents |
| `result` | Raw result payloads — prefer the formatted `room_outcome` |
| `system` | Internal lifecycle events (session start, contributor pipeline) |

**Configurable (adapter or user preference):**

| Event type | Default | Notes |
|---|---|---|
| Tool call summaries | Off | Compact `[tool] name: detail` lines can be useful for debugging but create noise during normal operation |
| Streaming text deltas | Buffer then send | Buffer `content_block_delta` text fragments and flush periodically (existing pattern: 1.5s flush interval) rather than sending each delta |
| Session lifecycle | Start + stop only | Post a message at session start and end; intermediate state transitions are internal |

### Failure Modes and Permission Concerns

**Failure modes:**

- **Thread not found / deleted** — The platform thread may be deleted or archived externally. Adapters must handle `404`/`not_found` errors gracefully: log, deregister the route, and optionally notify via a fallback channel.
- **Rate limiting** — All platforms impose rate limits. Outbound messages must pass through a per-platform rate limiter with exponential backoff. Inbound processing is less constrained but should still respect API polling limits (relevant for Slack `conversations.replies`).
- **Echo loops** — If mirrored output is re-ingested as user input, it creates an infinite loop. The `source_platform` stamp on inbound events prevents this: the broker ignores `user_confirmed` events where `source_platform` matches the originating channel.
- **Session start race** — An inbound message may arrive before the session's route is fully registered (e.g., user replies to a topic-creation message). A short grace-period queue (configurable, default ~5 seconds) absorbs this race.
- **Stale routes** — If the session crashes without clean shutdown, routes become orphaned. A periodic reconciler should scan for routes whose `session_id` is no longer active and clean them up.
- **Message ordering** — Platform delivery order is not guaranteed under load. For critical sequences (permission request followed by action), use explicit request IDs rather than relying on message order.

**Permission concerns:**

- **Thread-level ACL** — Not all platform users in a chat should be able to inject commands into a session. The route's sender validation step must check the user against the session's owner and any configured operator list.
- **Bot permissions** — Creating threads requires elevated bot permissions on most platforms (admin on Telegram, `CREATE_*_THREADS` on Discord, app installation on Teams). Adapters should detect missing permissions at bind time and fall back to `shared_chat` mode with a warning.
- **Cross-tenant isolation** — In multi-tenant deployments, a route must be scoped to its tenant. A thread in Tenant A's Slack workspace must never route to a session owned by Tenant B. Enforce this by including `tenant_id` in route lookup keys.
- **Credential scoping** — Bot tokens used by channel adapters must be scoped to the minimum permissions required. Telegram bots should not have `can_delete_messages` unless thread cleanup is explicitly enabled. Slack apps should request only `chat:write` and `channels:history`, not `admin.*`.

## Sources

- Telegram Bot API — Forum Topics (core.telegram.org/bots/api, retrieved 2026-05-05)
- Slack API — chat.postMessage, conversations.replies, Events API (docs.slack.dev, retrieved 2026-05-05)
- Discord Developer Docs — Threads, Gateway Events, Rate Limits (docs.discord.com/developers, retrieved 2026-05-05)
- Microsoft Teams — Bot Framework Channel Conversations (learn.microsoft.com, retrieved 2026-05-05)
- Matrix Spec v1.13 — Threading via m.thread relation (spec.matrix.org/v1.13, retrieved 2026-05-05)

<!-- sources: src_niu777_telegram_bot_api, src_niu777_slack_api, src_niu777_discord_api, src_niu777_teams_bot_fw, src_niu777_matrix_spec -->
