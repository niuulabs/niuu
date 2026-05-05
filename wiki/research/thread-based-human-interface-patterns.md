---
type: research
confidence: high
produced_by_thread: true
related_entities: []
source_ids: [src_niu777_telegram_bot_api, src_niu777_slack_api, src_niu777_discord_api, src_niu777_whatsapp_cloud_api, src_niu777_teams_bot_fw, src_niu777_matrix_spec]
---

# Thread-Based Human Interface Patterns for Active Sessions

> **TL;DR** — Map each agent session to exactly one platform thread using a generic `CommunicationRoute` envelope that carries platform, conversation ID, thread ID, and direction metadata. Mirror public-facing agent output and human-approval prompts; keep internal tool calls, thinking blocks, and inter-agent mesh traffic internal.

## Compiled Truth

### Recommended Generic Routing Model

Every active session maintains zero or more `CommunicationRoute` records linking it to external threads.

**Route lifecycle:**

1. **Bind** — Adapter resolves or creates a platform thread and registers a route on the broker.
2. **Dispatch outbound** — Broker filters events through a mirror policy, formats, and sends.
3. **Dispatch inbound** — Adapter matches inbound events to a session via the route, validates the sender, and injects into the session input stream.
4. **Unbind** — Route deregistered on session stop. Thread may be closed, archived, or left open per adapter policy.

**Route envelope (generic):**

```
CommunicationRoute {
  platform:        string        // "telegram" | "slack" | "discord" | "whatsapp" | "teams" | "matrix"
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

Extends the `CommunicationRoute` domain model (`src/volundr/domain/models.py`) and `TelegramChannel.communication_route()` (`src/skuld/channels.py`). The domain model already carries `owner_id`, `active`, `default_target`, and timestamps. Current `CommunicationPlatform` values: telegram, slack, discord, whatsapp. Teams and matrix are proposed additions.

**Inbound routing:** Extract `(platform, conversation_id, thread_id)` → look up route → validate sender against session ACL → inject as `user_confirmed` with `metadata.source_platform` set (prevents echo loops). No match → queue briefly or drop.

### Platform Thread Identifier Reference

| Platform | Thread ID field | Type | Create method | Inbound delivery |
|---|---|---|---|---|
| Telegram | `message_thread_id` | Integer | `createForumTopic` | Webhook / `getUpdates` — filter by `message_thread_id` |
| Slack | `thread_ts` | String | `chat.postMessage` with `thread_ts` of parent | Events API `message` event with `thread_ts` present |
| Discord | Channel `id` | Snowflake (String) | `POST /channels/{id}/threads` | Gateway `MESSAGE_CREATE` in thread channel |
| WhatsApp | `context.message_id` (reply-to) | String | Cloud API `messages` endpoint — contextual replies only, no forum threads | Webhook `messages` notification filtered by `from` + `wa_id` |
| Teams | `conversation.id` (contains `messageid=`) | String | `CreateConversationAsync` / Bot Framework | Bot Framework webhook Activity |
| Matrix | `event_id` of root | String | Send event with `m.relates_to.rel_type: "m.thread"` | `/sync` or appservice transaction — filter `m.relates_to` |

### Per-Platform Notes

**Telegram**
- Forum topics require a **supergroup** with forum mode enabled and bot **admin rights**. General topic has fixed `message_thread_id = 1`.
- Since Bot API 9.4 (February 2026), `createForumTopic` also works in **private chats** — bots can create topics in 1-on-1 DMs. Admins can restrict this via `allows_users_to_create_topics`. Latest Bot API version: 9.6 (April 2026).
- Topic name max: 128 characters. Truncate session names accordingly.
- Rate limit: ~30 msg/s globally, ~1 msg/s per chat. Buffer and batch outbound.
- Three topic modes: `shared_chat`, `fixed_topic`, `topic_per_session`. The existing `TelegramChannel` implements all three.
- `closeForumTopic` signals session end without deleting history — preferred for archival.

**Slack**
- Threads are implicit: post a message, reply to its `ts`. Post a session-start message, capture its `ts`, use as `thread_ts` for all subsequent messages.
- `reply_broadcast: true` surfaces key messages in the channel timeline — use sparingly.
- Replies arrive as `message` events where `thread_ts != ts`. Aggressive rate limits on `conversations.replies` for non-Marketplace apps — as of March 2026, posted limits also apply to existing installations distributed outside the Slack Marketplace. Prefer event-driven inbound over polling.
- Bot tokens can only call `conversations.replies` on DMs and group DMs; public/private channel threads require a user token with `channels:history` or `groups:history` scopes.
- No thread-specific subscription — app receives all channel events, must route by `thread_ts`.

**WhatsApp**
- No forum-style threads, but the Cloud API supports **contextual replies** — send with `context.message_id` to quote a previous message in a reply bubble.
- Use `shared_chat` mode with `thread_id = None`; route by `conversation_id` (`wa_id`). Store the last outbound `message_id` in route metadata for reply-to correlation.
- `CommunicationPlatform.WHATSAPP` already exists in the codebase.
- Meta prioritises Cloud API — new features arrive weeks before On-Premise. Outbound requires approved message templates for the first 24-hour window; rate limits depend on business tier.

**Discord**
- Threads are first-class Channel objects (types 10, 11, 12). Public and private thread types exist.
- Set `auto_archive_duration` to 10080 (7 days) for sessions and extend on activity.
- Bots need `SEND_MESSAGES_IN_THREADS` permission. Messages arrive via `MESSAGE_CREATE` gateway event where `channel_id` is the thread ID.
- Global rate limit: 50 req/s per bot; per-route limits in response headers.
- Forum channels are a natural fit for topic-per-session: each session becomes a forum post with its own thread.

**Microsoft Teams**
- Thread identity is in `conversation.id` as a `messageid=` suffix — extract this, not `replyToId`.
- Bots only receive messages when **@mentioned** unless the app has RSC `ChannelMessage.Read.Group`.
- Proactive messaging requires a stored `ConversationReference` with `serviceUrl`, `conversationId`, `tenantId`, and `userId`/`aadObjectId`. Rate limit: ~4 msg/s per conversation.
- No native "close thread" — signal session end via a final message.

**Matrix**
- Threads use the `m.thread` relation type (stable since spec v1.4, September 2022).
- Nested threads are explicitly forbidden — the root must be a non-thread event.
- Thread replies should set `m.in_reply_to` referencing the latest thread message with `is_falling_back: true` — this provides backward-compatible context for clients that do not support `m.thread`.
- Application services are typically exempt from rate limiting, making Matrix well-suited for high-throughput mirroring.
- Thread history: `GET /rooms/{roomId}/relations/{eventId}/m.thread` (paginated). Thread summaries (latest event, participant list, total count) are available via aggregation.

### Required Route Metadata

Minimum metadata for reliable routing:

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
| `content_block_delta` (thinking) | Internal reasoning — noisy and exposes chain-of-thought |
| `assistant` tool_use blocks | Implementation detail |
| `room_message` (visibility=internal) | Inter-agent coordination |
| `room_mesh_message` | Delegation/orchestration traffic between agents |
| `result` | Raw payloads — prefer formatted `room_outcome` |
| `system` | Internal lifecycle events |

**Configurable:** Tool call summaries (off by default), streaming text deltas (buffer then flush at the 1.5s interval), and session lifecycle events (start + stop only by default).

### Failure Modes and Permission Concerns

**Failure modes:**

- **Thread not found / deleted** — Adapters must handle `404`/`not_found` gracefully: log, deregister the route, optionally notify via fallback.
- **Rate limiting** — Outbound must pass through a per-platform rate limiter with exponential backoff.
- **Echo loops** — `source_platform` stamp on inbound events prevents mirrored output from being re-ingested.
- **Session start race** — Inbound messages arriving before route registration are absorbed by a short grace-period queue (default ~5s).
- **Stale routes** — Session crash without clean shutdown orphans routes. A periodic reconciler should scan for routes whose `session_id` is no longer active.

**Permission concerns:**

- **Thread-level ACL** — The route's sender validation must check users against the session's owner and configured operator list.
- **Bot permissions** — Thread creation requires elevated permissions (admin on Telegram, `CREATE_*_THREADS` on Discord, app install on Teams). Detect at bind time and fall back to `shared_chat`.
- **Cross-tenant isolation** — A route must be scoped to its tenant. Enforce this by including `tenant_id` in route lookup keys.
- **Credential scoping** — Bot tokens must use minimum permissions. Avoid `can_delete_messages` on Telegram unless thread cleanup is explicitly enabled.

## Timeline

- 2026-05-05: Initial research compiled covering Telegram, Slack, Discord, WhatsApp, Teams, and Matrix threading patterns.
- 2026-05-05: Updated with Telegram Bot API 9.4+ private-chat forum topics, WhatsApp contextual reply-to support, Slack March 2026 rate-limit changes for non-Marketplace apps, Matrix `is_falling_back` guidance, and Teams ConversationReference field additions.

## Sources

- Telegram Bot API 9.6 — Forum Topics, createForumTopic (core.telegram.org/bots/api, retrieved 2026-05-05)
- Slack API — chat.postMessage, conversations.replies, Events API (docs.slack.dev, retrieved 2026-05-05)
- Discord Developer Docs — Threads, Gateway Events, Rate Limits (docs.discord.com/developers, retrieved 2026-05-05)
- WhatsApp Cloud API — Messages, Webhooks, Contextual Replies (developers.facebook.com/docs/whatsapp/cloud-api, retrieved 2026-05-05)
- Microsoft Teams — Bot Framework Proactive Messaging, ConversationReference (learn.microsoft.com, retrieved 2026-05-05)
- Matrix Spec v1.4+ — Threading via m.thread relation, MSC3440 (spec.matrix.org, retrieved 2026-05-05)

<!-- sources: src_niu777_telegram_bot_api, src_niu777_slack_api, src_niu777_discord_api, src_niu777_whatsapp_cloud_api, src_niu777_teams_bot_fw, src_niu777_matrix_spec -->
