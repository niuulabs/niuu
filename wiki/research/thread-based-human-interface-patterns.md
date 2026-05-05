---
type: research
confidence: high
produced_by_thread: true
related_entities: []
source_ids: [src_telegram_bot_api, src_slack_api, src_discord_api, src_teams_bot_framework]
---

# Thread-Based Human Interface Patterns for Active Sessions

> **TL;DR** — Map each active agent session to exactly one platform thread using a `(platform, conversation_id, thread_id)` route tuple. Decouple route lifetime from session lifetime so threads survive restarts. Mirror public room events outward; keep internal deliberation, tool calls, and thinking blocks internal.

## Compiled Truth

### Recommended Generic Routing Model

The routing model centres on a **communication route** — a persistent record that binds an external thread to a session:

| Field | Type | Purpose |
|---|---|---|
| `platform` | enum | Channel type (`telegram`, `slack`, `discord`, `teams`) |
| `conversation_id` | string | Top-level channel or chat identifier |
| `thread_id` | string or null | Platform-specific thread/topic identifier |
| `session_id` | UUID | Target Volundr session |
| `owner_id` | string | User who established the route |
| `mode` | enum | `room` (broadcast) or `directed` (single peer) |
| `active` | bool | Whether the route is accepting messages |
| `metadata` | JSON | Platform-specific extras (topic name, parse mode, etc.) |

**Lookup path:** Inbound message arrives with `(platform, conversation_id, thread_id)`. The route repository resolves this to a `session_id` using an exact match (with `IS NOT DISTINCT FROM` semantics on nullable `thread_id`). The most recently updated active route wins when multiple matches exist.

**Thread-per-session is the default topology.** Each session creates or claims one thread at start time. This avoids cross-talk between sessions and gives humans a clear navigational anchor. The three supported modes are:

- **`topic_per_session`** — Bot creates a new thread/topic when the session starts. Best for ongoing work where each session deserves its own conversation space.
- **`fixed_topic`** — Session binds to a pre-existing thread. Useful for long-lived channels where a human pre-creates the topic.
- **`shared_chat`** — No threading; all messages land in the main chat. Acceptable for single-session use cases but does not scale.

**Route lifecycle should outlive sessions.** Routes are currently deactivated when a session stops. To support session restart and reforge, routes should transition to a `dormant` state rather than being deactivated. On restart, the session reclaims its dormant route and resumes posting to the same thread. This preserves conversation continuity for humans who are following the thread.

### Inbound Reply Routing

When a human replies inside a thread, the platform delivers the message to the bot with the thread identifier attached. The ingress service normalises this into an `InboundCommunicationMessage` with platform, conversation_id, thread_id, sender identity, and text. The route lookup then maps it to the correct session.

**Safety rules for inbound routing:**

- **Validate sender identity.** Only route messages from users authorised for the session's tenant. Drop messages from unknown senders with a log entry.
- **Rate-limit inbound messages.** Apply per-route rate limiting to prevent a human from flooding a session. A reasonable default is 10 messages per minute per route.
- **Tag source platform on injected messages.** When an inbound message enters the session room, include `source_platform` and `sender_external_id` in metadata so agents and the room UI can attribute it correctly.
- **Directed routing.** If the message starts with `@peer_name`, route it to that specific participant. Otherwise broadcast to the room.

### Required Route Metadata

Beyond the core tuple, each route should carry platform-specific metadata:

| Platform | Required metadata |
|---|---|
| Telegram | `topic_mode`, `topic_name`, `notify_only`, `bot_token_ref` |
| Slack | `workspace_id`, `bot_user_id`, `reply_broadcast` (bool) |
| Discord | `guild_id`, `parent_channel_id`, `auto_archive_duration` |
| Teams | `service_url`, `tenant_id`, `bot_app_id` |

All platforms should also store `created_at` and `last_message_at` for staleness detection.

### Mirroring Rules — What Goes Out, What Stays In

| Event type | Mirror to thread? | Rationale |
|---|---|---|
| Room messages (public) | Yes | Human-visible agent output |
| Room notifications | Yes | Alerts that need human attention |
| Room outcomes | Yes | Final results, verdicts, summaries |
| User-confirmed prompts | Yes (from non-platform sources) | Shows human inputs from other surfaces |
| Error events | Yes | Humans need to see failures |
| Thinking blocks | No | Internal reasoning, not actionable |
| Tool calls / results | No (summary only) | Raw tool traffic is noisy; optionally send a one-line `[tool] name: detail` summary |
| Internal room messages | No | Agent-to-agent coordination |
| Content block deltas | Buffer, then send | Stream as periodic flushes, not per-delta |
| Permission requests | Yes (with action buttons) | Humans must be able to approve/deny |

**Principle:** Mirror everything a human sitting in front of the session UI would see. Suppress everything that belongs to the agent's internal working process.

### Per-Platform Notes

**Telegram.** Forum topics are the natural thread primitive. Topics use integer `message_thread_id` values derived from the topic creation message. The General topic (id=1) cannot be deleted. Rate limits are approximately 20 messages per minute per group. Messages exceeding 4096 characters must be split. Topics are flat — no nested threading. Bot must be admin with `can_manage_topics` to create topics (Telegram Bot API, retrieved 2026-05-05).

**Slack.** Threads are implicit — the first reply to a message creates a thread, identified by the parent message's `thread_ts` (a string timestamp like `"1503435956.000247"`). There is no explicit create/close/archive API for threads. `reply_broadcast=true` surfaces a reply in the main channel timeline. Rate limit is approximately 1 message per second per channel. Passing an invalid `thread_ts` silently creates a new top-level message rather than erroring (Slack API docs, retrieved 2026-05-05).

**Discord.** Threads are full channel objects with Snowflake IDs. Forum channels are thread-only — every post is a thread. Auto-archive durations are 60 minutes, 1 day, 3 days, or 7 days. Sending a message to an archived thread auto-unarchives it (unless locked). Global rate limit is 50 requests per second per bot. Bots cannot create forum channels via API — they must be created manually (Discord API docs, retrieved 2026-05-05).

**Microsoft Teams.** Threading uses `conversationId` + `activityId` as a composite identifier. `replyToId` is unreliable — Teams often omits it on inbound activities even within a thread. Bots only receive messages when @mentioned unless RSC permission `ChannelMessage.Read.Group` is granted. Private channel messaging is not supported. The `serviceUrl` can change between messages, so it must be refreshed from recent activities (Microsoft Bot Framework docs, retrieved 2026-05-05).

### Failure Modes and Permission Concerns

- **Thread creation failure.** If topic/thread creation fails (permissions, rate limit, API error), fall back to `shared_chat` mode and log a warning. Do not block session start.
- **Route collision.** Two sessions claiming the same thread should be prevented by the uniqueness constraint on `(platform, conversation_id, thread_id, active=true)`. If a collision is detected, the newer session should fail to bind and surface an error.
- **Stale routes.** Routes whose `last_message_at` exceeds a configurable threshold (e.g. 24 hours) should be eligible for automatic deactivation to prevent orphaned threads from accumulating.
- **Permission escalation.** Inbound messages must not bypass session-level authorization. The route's `owner_id` establishes the trust boundary — messages from senders outside the session's tenant should be dropped.
- **Platform API outages.** Outbound message failures should be retried with exponential backoff (max 3 retries). Persistent failures should degrade the channel to `notify_only` mode rather than crashing the session.
- **Message ordering.** Platform APIs do not guarantee delivery order. For critical sequences (e.g. permission request → response), include correlation IDs and validate ordering on receipt.

## Sources

- Telegram Bot API — Forum topic methods (core.telegram.org/bots/api, retrieved 2026-05-05)
- Slack API — chat.postMessage, conversations.replies (docs.slack.dev, retrieved 2026-05-05)
- Discord API — Threads documentation (docs.discord.com/developers/topics/threads, retrieved 2026-05-05)
- Microsoft Bot Framework — Channel and group conversations (learn.microsoft.com, retrieved 2026-05-05)
- Volundr codebase — `src/skuld/channels.py`, `src/volundr/domain/services/communication_ingress.py`, `src/volundr/domain/models.py` (internal, 2026-05-05)

<!-- sources: src_telegram_bot_api, src_slack_api, src_discord_api, src_teams_bot_framework -->
