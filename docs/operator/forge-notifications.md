# Forge session notifications

Forge keeps one durable, cursor-ordered notification feed per node. Sessions, Forge
itself and operators all write into it. The web app, Lexi and external controllers read
it over REST and receive new entries live over the existing session SSE stream. Delivery
rules can forward selected notifications to external sinks, such as Telegram, push or a
webhook, through a durable outbox.

The binding interfaces are in
[`docs/forge/session-notifications-contract.md`](../forge/session-notifications-contract.md).
This page covers how to operate the feature.

## Where notifications come from

| Source | Kind(s) | How it is recorded | Dedupe key |
|---|---|---|---|
| `agent` | milestone, decision, attention, error, info | The model calls the Forge MCP `notify` tool. Skuld appends a notification turn to the session log, and Forge projects it when the log batch is appended. | `turn:{session}:{turn_id}:{kind}` |
| `system` | reply_ready | **Off by default** (`notifications.reply_ready.enabled`). When on: every final assistant reply (the same rule that drives the session inbox). The title is the reply's first line and the body is a bounded excerpt. | `turn:{session}:{final_turn_id}:reply_ready` |
| `system` | attention | A session starts waiting for its owner (a question, confirmation or permission request). | `attention:{session}:{state_since}[:{request_id}]` |
| `operator` | any except reply_ready | `POST /api/v1/forge/sessions/{id}/notifications` | `submit:{session}:{principal}:{idempotency_key}` |
| `agent` (direct) | milestone, decision, attention, error, info | The same direct submit made by the session itself with its own session credential, for example through the Forge-hosted MCP endpoint | `submit:{session}:agent:{owner}:{idempotency_key}` |
| `system` | attention | A resident is waiting for its owner (an operator question). Read from the resident's notification outbox; see [Residents](#residents). | `resident-attention:{resident}:{channel or room}:{request_id}` |
| `agent` | milestone, decision, attention, error, info | A resident's agent calls its `notify` tool. Read from the resident's notification outbox. | `resident-notify:{resident}:{channel or room}:{idempotency_key}` |

The notification id is a uuid5 of the dedupe key, so every retry maps to the same
notification. A retried log append, activity report or submit never creates a second
row, a second delivery or a second live event.

Projection from the session log runs after the idempotent insert and conflict detection,
and before the append is acknowledged. It always reads the stored rows back. A frame that
re-uses a stored seq with a different payload (a `log_conflict`) is never projected. The
insert and the delivery scheduling happen in one transaction, so a committed notification
always has its deliveries scheduled.

The attention key carries the pending `request_id` when the broker reports one. A second
question raised while a session is already waiting keeps the same `state_since`, and
without the request id it would be dropped as a duplicate of the first.

Ownership, meaning the owner, tenant, project, session name, engine and model, is copied
from the session row at projection time. Notifications have no foreign key to sessions, so
they outlive a deleted session.

## Reading the feed

All routes are under `/api/v1/forge`. The Guild facade serves the same paths.

| Method and path | Purpose |
|---|---|
| `GET /notifications` | The caller's feed. See the query parameters below. |
| `GET /notifications/read-state` | `{read_through_seq, revision, unread_count, head_seq}` |
| `PUT /notifications/read-state` | `{read_through_seq, expected_revision}`. The watermark only moves forward. A stale `expected_revision` returns `409`. A `read_through_seq` beyond the newest notification you can see returns `422`. |
| `GET /sessions/{id}/notifications?after=&limit=` | One session's notifications as a JSON list, ascending. Requires read access to the session. |
| `POST /sessions/{id}/notifications` | Direct submit: a `NotificationDraft` plus `idempotency_key`. Returns `201` for a new notification, `200` for a deduplicated one. `session_seq` is always null. Requires the session's `emit_event` permission, the same as appending to its log. The source is `agent` when the session submits for itself with its session credential, and `operator` otherwise. |
| `GET/POST /notifications/rules`, `PUT/DELETE /notifications/rules/{id}` | Your own delivery rules. |
| `GET /notifications/sinks` | Sinks a rule may name: `[{name, label, requires_integration}]` |
| `GET /notifications/{id}/deliveries` | Outbox rows for a notification you can see. |

`GET /notifications` accepts these query parameters:

- `limit`: defaults to `notifications.default_page_size`, up to `max_page_size`.
- One cursor, never both:
  - `before=<seq>` pages newest first. The response's `next_before` is the next page's
    cursor, or null at the end.
  - `after=<seq>` pages ascending, for gap-fill after a reconnect.
- Filters: `kind` and `source` (comma-separated), `min_severity`, `session_id`,
  `project_id`, and `unread=true|false`. Unknown values return `422`.
- The response is `{items, next_before, head_seq, read_through_seq, unread_count}`. Each
  item is read through the host watermark or an individual acknowledgement. The counters cover your whole scope and
  ignore the filters.

**Scope.** A non-admin sees only notifications they own. A `volundr:admin` also sees every
notification in their own tenant, plus untenanted ones. The feed, the SSE filter and the
Guild facade all apply this one rule (`niuu.domain.notifications.notification_visible_to`).

**Ordering.** `seq` is a per-node `BIGSERIAL`. Each projection transaction takes a
transaction-scoped advisory lock before it inserts, so seqs become visible in increasing
order. A reader paging with `after=<seq>` therefore never skips a notification that
committed late. `created_at` is set from `clock_timestamp()` after the lock is taken, so it
increases with `seq`.

### Deep links into the transcript

Every notification carries `turn_id` (null when it has no turn). Resolve it with
`GET /api/v1/forge/sessions/{id}/conversation/turns/{turn_id}`: the response adds `index` and
`total_turns`, so a client opens the conversation window at that turn with
`?after=<index-1>`. The raw log read (`/sessions/{id}/log`) never returns
`conversation.turn` rows by design, so do not anchor on log sequence numbers.

## Residents

A resident (a long-lived Ravn) is not a Forge session, so it has no session log or
activity reports to project from. It keeps a small durable notification outbox
instead (in its OpenClaw shim's state database): the operator questions it waits on,
their answers, and what its agent sends with its `notify` tool. Forge follows the
outbox of every active resident that serves sessions, over the same connection it
drives the resident's channels with, authenticated by the resident's own shim token
from the credential store. So it works on every resident backend (helmrelease and
OpenShell) with nothing to configure, and the resident holds no Forge credential.

- **Attention.** When the resident asks its owner a question (`ask_operator`), Forge
  records an `attention` item from `source: system`, the same item a waiting session
  raises, so the owner's **Needs-input pushes** rule delivers it. When the owner
  answers (in the room, through Telegram or the API), the item is marked read.
  Questions still waiting when the resident starts are raised once.
- **Notify.** The resident's agent has a `notify` tool, the counterpart of the Forge
  MCP `notify`. Its notifications are recorded as `source: agent`.
- **Anchor.** `session_id` is null. The item carries `resident_id` and
  `resident_session_id` (the channel it came from; null for the main room) in the
  feed, the live event, webhook payloads and push payloads. Deliveries link to
  `notifications.resident_link_path` / `resident_session_link_path`.
- **Delivery.** The same rules, outbox, dispatcher and sinks as for sessions.

Forge acknowledges what it recorded, and the resident then drops it. Every recording
is idempotent, so a reconnect or several Forge replicas never produce a duplicate.

```yaml
resident_runtimes:
  notifications:
    enabled: true              # needs notifications.enabled
    batch_size: 100
    idle_resync_seconds: 60    # pull again without a change event
    reconnect_initial_seconds: 5
    reconnect_max_seconds: 300 # also how often a resident without an outbox is rechecked
```

On the resident, `gateway.channels.openclaw.notifications_enabled` (default true)
serves the outbox and offers `notify`; `notifications_max_pending` (default 1000)
bounds what is kept while Forge does not drain it. A resident whose shim is off
(`resident.openclaw.enabled: false`) has no outbox.

## Live events

A new notification is broadcast on `GET /api/v1/forge/sessions/stream` as
`event: session_notification`. Its `data` is the notification without `read`. Forge sends
it only to the owner, or to an admin of the notification's tenant. A client without an
identity gets no notification events. Other event types are unchanged.

Every node applies the same rule. The Guild facade also re-checks each event before
forwarding it, so an older node cannot leak another owner's notification into the fleet
stream.

The stream is not durable. After a reconnect, fill the gap with
`GET /notifications?after=<last seen seq>`.

When Sleipnir is enabled, each new notification is also published as
`volundr.session.notification`. The payload is the notification, and its urgency follows
the severity: `critical` > `warning` > `info`/`success`. Consumers must honour `owner_id`.

## The Guild facade (all hosts)

Without `all_instances=true`, requests go to the host named by `instance_id`, or else to
the default (local) host. `scope=local` pins the embedded host.

`GET /notifications?all_instances=true` fans out to every visible host. It merges their
pages newest first by `(created_at, instance_id, seq)`, and each host's rows keep their
seq order.

- **Cursors** are opaque: unpadded base64url JSON of `{instance_id: seq}`.
  - `next_before` pages older. A host that is missing from it has nothing older left, and
    a `null` position means "from that host's newest".
  - `after` takes the same shape for gap-fill. Only the hosts named in an `after`
    cursor are read; send a host with seq `0` to read it from the start.
  - `next_after` is the gap-fill cursor to keep. It holds the newest seq per host.
- **Response fields.**
  - `unread_count` is summed across hosts, and the per-host counters are under
    `instances`.
  - The top-level `head_seq` and `read_through_seq` are `null`, because seqs are per
    host.
  - Each item carries `instance_id`, `instance_name` and `instance_slug`.
- **Failures.**
  - Hosts that fail or answer garbage are listed in `X-Forge-Unavailable-Instances`.
    They keep their cursor position so the next page retries them.
  - If every host fails, the response is `502`.
  - A request error that every host would repeat, such as a bad filter, is returned as
    is.

Read state works the same way:

- `GET /notifications/read-state?all_instances=true` returns
  `{unread_count, instances: {id: state}}`.
- `PUT /notifications/read-state` accepts either the single-host body or
  `{"instances": {id: {read_through_seq, expected_revision}}}`. If any host reports a
  revision conflict, the response is `409` with
  `detail: {conflicts: [...], instances: {...applied}}`.

Session routes go to the host that owns the session. Rules and sinks go to the selected
host (local by default). Deliveries are looked up on the host that holds the
notification.

### Finding the Guild from a node

A node's own feed covers only that node: it has no `instances` and ignores
`all_instances`. A client that talks to a node directly asks it for its Guild:

```
GET /api/v1/forge/guild
{"guild_url": "https://yggdrasil.niuu.world",
 "instance_id": "ae40cfda-426a-46b4-a080-1ad668000859",
 "instance_name": "Valhalla", "instance_slug": "valhalla"}
```

and reads the merged feed from `guild_url`, where this node's items and read state are
keyed by `instance_id`. The route is authenticated like every Forge route and answers
`404` when the node belongs to no Guild. The node also puts the same three
`instance_*` fields on every item of its own feed and on its live `session_notification`
events (`null` / absent when it belongs to no Guild).

The values come from the node's `guild:` config section:

- a host joined with `niuu join` gets them written by the CLI from the join response
  (`niuu guild heartbeat` fills them in for hosts joined before this);
- a cluster its Guild registers from configuration (the Guild's `niuu.instances`) sets
  the Volundr chart's `guild.url`, `guild.instanceId`, `guild.instanceName` and
  `guild.instanceSlug` from the Guild's registry (`GET /api/v1/niuu/targets/volundr`).

A Forge node with `guild.url` but no instance keeps running: its items carry `null`
instance fields and only `GET /api/v1/forge/guild` reports it, with `503` and the remedy
(`niuu guild heartbeat --once` on a joined host, the chart's `guild.instance*` values on a
cluster). Through the Guild facade
the same route describes the Guild itself: `guild_url` is the Guild chart's `guild.url`
(its own public origin), and the instance is its embedded node, else its default node,
else `null`.

## Delivery rules and the outbox

A rule selects notifications from its owner and names a sink:

```json
{"name": "Decisions to Telegram", "enabled": true,
 "match": {"kinds": ["decision", "attention"], "min_severity": "warning",
           "sources": [], "project_ids": [], "session_ids": []},
 "sink": "integration", "integration_connection_id": "conn-123",
 "config": {"rate_limit": {"max_count": 10, "window_seconds": 3600}},
 "quiet_hours": {"start": "22:00", "end": "07:00", "timezone": "Europe/London",
                 "allow_min_severity": "critical"}}
```

- In `match`, an empty list means "any".
- `sink` must be one of the configured `notifications.sinks` names. The exception is a
  rule that sets `integration_connection_id`: that must be one of the caller's enabled
  messaging integrations, and its credentials and adapter are used. Such rules use the
  sink name `integration`.
- `quiet_hours` is a daily window in an IANA timezone. A window whose start is after its
  end spans midnight.
- `config.rate_limit` caps the deliveries per rule within a window.

When a notification is recorded, every matching enabled rule gets a `pending` row in
`forge_notification_deliveries`, in the same transaction. Nothing is delivered externally
unless a sink is configured and a rule selects it.

### How the dispatcher settles a delivery

The outbox dispatcher is a background loop in every Forge host
(`notifications.dispatcher.enabled`, on by default; it is idle until a rule exists). Each
poll it claims up to `batch_size` due rows with `FOR UPDATE SKIP LOCKED` and a lease of
`lease_seconds`, then settles each one:

| Outcome | Status | When |
|---|---|---|
| Sent | `delivered` | The sink accepted it. `delivered_at` is set. |
| Held back | `suppressed` | The rule was disabled after scheduling, quiet hours applied, the rate limit was reached, or the sink declined it (see push below). `last_error` holds the reason. |
| Transient failure | `failed` | A 408/425/429/5xx response, a network error, a timeout, or a sink that is not configured on this host. Retried at `next_attempt_at`. |
| Permanent failure | `dead` | Any other 4xx, a missing or disabled integration, a missing credential, or `max_attempts` reached. Never retried. |

- **Order of checks, at claim time:** the attempt count, the rule's `enabled` flag, quiet
  hours, the rate limit, sink resolution, then the send. A suppressed row is kept for
  audit and never retried.
- **Quiet hours** are evaluated in the rule's timezone. Notifications at or above
  `allow_min_severity` (default `critical`) are still delivered.
- **Rate limit.** `config.rate_limit` = `{max_count, window_seconds}` counts the rule's
  `delivered` rows in the trailing window. Rules without one use
  `notifications.dispatcher.default_rate_limit` (60 per hour; `null` turns the default
  off). Messages over the limit are `suppressed`, not delayed. Several dispatchers
  counting at once can overshoot by at most one poll's worth of sends.
- **Backoff.** Retry *n* waits `backoff_base_seconds × 2^(n-1)`, capped at
  `backoff_max_seconds`, spread by ±`backoff_jitter_ratio`. A provider's `Retry-After`
  (Telegram's `retry_after`) is honoured when it asks for longer.
- **Leases and crashes.** A send runs for at most `send_timeout_seconds`, and only starts
  while at least that much of its lease remains, so a slow batch never overlaps a
  re-claim. If a host dies mid-send, the row stays `claimed` until its lease expires, and
  then any dispatcher re-claims it. Every settle is fenced by `(delivery id, attempts)`,
  so a worker whose lease expired cannot overwrite a newer claim.
- **At-least-once.** A send that timed out, or a crash between a send and its settle, is
  sent again. Webhook receivers should de-duplicate on `X-Niuu-Delivery`.
- **Shutdown.** Stopping Forge stops polling and gives in-flight sends
  `send_timeout_seconds` to finish. Anything left is re-claimed after its lease.

Any number of Forge hosts can share one database. Each row is sent by exactly one of
them.

## Delivery setup

### Telegram, through the owner's integration

Telegram needs no Forge config. Each user connects their own bot:

1. Create a bot with @BotFather and note its token. Add the bot to the chat, group or
   channel that should receive messages, and find the chat id (for example, send a
   message and read `getUpdates`).
2. In the web app, open **Settings → Integrations**, add **Telegram**, and enter the bot
   token and chat id. This stores the credential in the credential store and creates a
   `messaging` integration connection owned by the user. `message_thread_id` in the
   connection config posts to a forum topic.
3. In **Notifications → Rules**, create a rule whose sink is **Messaging integration**
   and pick the Telegram connection (`sink: "integration"`,
   `integration_connection_id: <connection id>`).

The dispatcher resolves the connection at send time. It checks that the connection
still exists, belongs to the rule's owner, is enabled and is a messaging integration,
loads its credential, and builds the sink named for the connection's slug in
`notifications.integration_sinks` (Telegram maps to
`niuu.adapters.notifications.telegram.TelegramNotificationSink`). The same connection keeps
working for Ting's run notifications. Connections created before this release, whose
stored adapter is empty or the old `ting.adapters.telegram_notification…` path, resolve
the same way.

Messages use Telegram HTML: a severity emoji and bold title, an italic context line
(severity, kind, session, host), the body, the notification's links, and **Open session
in Forge**. All text is escaped. Bodies are shortened to fit Telegram's 4096-character
limit. Telegram 429 and 5xx responses are retried (honouring `retry_after`). Other
errors, such as a wrong token (401), a bot removed from the chat (403) or an unknown chat
(400), make the delivery `dead` with Telegram's description.

### Webhook sink (operator-configured)

```yaml
notifications:
  sinks:
    - name: ops-webhook
      label: Ops webhook
      adapter: niuu.adapters.notifications.webhook.WebhookNotificationSink
      url: https://hooks.example.com/forge
      timeout: 10                          # seconds, optional
      headers: {X-Team: platform}          # optional extra headers
      secret_kwargs_env:
        secret: FORGE_OPS_WEBHOOK_SECRET   # env var holding the HMAC secret
```

Each delivery is a `POST` with `Content-Type: application/json`:

```json
{"type": "forge.notification", "version": 1, "delivery_id": "…", "attempt": 1,
 "notification": {"id": "…", "kind": "decision", "severity": "warning", "source": "agent",
   "title": "…", "body": "…", "owner_id": "…", "session_id": "…", "session_name": "…",
   "resident_id": null, "resident_session_id": null, "host": "…", "project_id": null, "url": "https://forge…/volundr/session/…",
   "links": [{"label": "PR", "url": "…", "kind": "pr"}], "engine": "claude",
   "model": "…", "correlation_id": null, "created_at": "ISO-8601"}}
```

Headers: `X-Niuu-Event: forge.notification`, `X-Niuu-Delivery: <delivery_id>` (stable
across retries) and, when a secret is set, `X-Niuu-Signature: sha256=<hex>`: the
HMAC-SHA256 of the exact request body. Verify it before trusting the payload. Any 2xx
is success. Redirects are not followed and count as permanent failures.

Users can also connect a webhook as a messaging integration (slug `webhook`, config
`url`, credential `secret`) and target it with an integration rule.

### Push (opt-in)

```yaml
notifications:
  sinks:
    - name: push
      label: Forge app push
      adapter: volundr.adapters.outbound.push_notification_sink.PushNotificationSink
      max_body_chars: 280                  # optional
push:
  enabled: true                            # the needs-input push (below)
  attention_sink: push                     # default; must name a notifications.sinks entry
  adapter: volundr.adapters.outbound.push_channels.ApnsNotificationChannel
  kwargs: {team_id: ABCDE12345, key_id: KEY1234567, bundle_id: com.niuu.forge}
  secret_kwargs_env: {private_key: APNS_PRIVATE_KEY}
```

The push sink delivers through the channel configured under `push:` (APNs, the webhook
relay, or the logging default) to the devices the owner registered. A rule that
targets it is the only way a notification reaches a phone.

**Needs-input push.** With `push.enabled`, the "session needs you" push is delivered by
this outbox like any other notification, so it is retried, recorded per delivery, and
honours quiet hours and rate limits. When an owner registers a device
(`POST /api/v1/forge/devices`), Forge gives them a **Needs-input pushes** rule:
`attention` notifications raised by Forge itself (`source: system`) → the sink named by
`push.attention_sink`. Forge adds it only when the owner has no rule for that sink yet
(enabled or not), so owners opt out by disabling it and keep any rule they made
themselves. If the rule cannot be added, the registration answers 500 and the app
registers again. `push.enabled` needs `notifications.enabled` and the named sink; Forge
refuses to start without them. Delivery runs on every host where
`notifications.dispatcher.enabled` is on.

In the Volundr Helm chart, set `push.enabled`, `push.adapter`, `push.kwargs` and
`push.secretKwargs` (`{kwarg, secretName, secretKey}`, mounted as `PUSH_SK_<KWARG>`).
The chart adds the push sink named by `push.attentionSink` to `notifications.sinks`
unless you already configured one by that name.

**Devices and keys outside mini mode.** Clusters keep integrations and their secrets in
one place, and push follows that. With `push.device_registry: auto` (the default; chart
`push.deviceRegistry`), a registered device outside mini mode is the owner's **messaging
integration** connection (slug `push-device`, listed and disableable with their other
integrations), and its token is stored in the credential store (OpenBao on the
clusters). Mini mode keeps the `device_tokens` table. Push devices are never attached to
sessions and are not built as Ting channels. Set `postgres` or `integrations` to choose
explicitly.

The APNs signing key belongs to the app, not to a user or tenant, so it is platform
configuration like any other app-wide secret: keep it in the cluster's secret source (for
example Doppler through an ExternalSecret) and hand it to `ApnsNotificationChannel` with the
chart's `push.secretKwargs`:

```yaml
push:
  enabled: true
  adapter: volundr.adapters.outbound.push_channels.ApnsNotificationChannel
  kwargs: {team_id: TEAMID, bundle_id: com.niuu.lexi.chat}
  secretKwargs:
    - {kwarg: key_id, secretName: volundr-apns, secretKey: key-id}
    - {kwarg: private_key, secretName: volundr-apns, secretKey: private-key}
```

A device that
registers with `environment` (`sandbox` for development builds, `production` for
TestFlight/App Store) is pushed through that APNs host; otherwise the channel default
(`use_sandbox`) applies. APNs requires HTTP/2, which the `httpx[http2]` extra provides.

APNs results: delivered if at least one device accepted. It is retryable when every
device failed transiently (429/5xx, network). It is `dead` when the owner has no iOS
devices or APNs rejected them all.

### Deep links

Messages link back to `notifications.public_web_url`, or else the host's public origin,
with `session_link_path` (default
`/volundr/session/{session_id}#notification-{notification_id}`, which opens the session
at the notification's card) or `feed_link_path` for sessionless notifications. Set
`public_web_url` whenever Forge is reached through a proxy or a public hostname. Relative
notification links (`/volundr/…`) are made absolute against the same base.

## Troubleshooting

Check a notification's delivery rows with `GET /api/v1/forge/notifications/{id}/deliveries`.
`status`, `attempts`, `next_attempt_at` and `last_error` explain every outcome. The
dispatcher logs each delivery, retry, suppression and dead row under
`volundr.domain.services.notification_dispatcher`. Logs and `last_error` never contain
bot tokens, webhook paths or secrets.

| Symptom | Likely cause |
|---|---|
| Rows stay `pending` | The dispatcher is off (`notifications.dispatcher.enabled`), or no Forge host with access to this database is running. |
| `suppressed`: "Quiet hours …" | Expected inside the rule's window. Raise the notification's severity, or change `allow_min_severity`. |
| `suppressed`: "Rate limit …" | The rule reached `config.rate_limit`, or the default of 60/hour. Raise it on the rule. |
| `failed`: "Sink 'x' is not configured on this host" | The rule names a sink missing from `notifications.sinks`. Add it and restart. (A configured sink that fails to build stops Forge at startup with "Notification sink … could not be built".) |
| `dead`: "Integration connection … is disabled / was not found" | The user disabled or deleted the integration. Re-enable it, or point the rule at another connection. |
| `dead`: "Credential '…' … was not found" | The integration's credential was deleted. Re-enter it in Settings → Integrations. |
| `dead`: "Telegram Bot API returned HTTP 401/403/400" | Wrong bot token, bot not in the chat, or wrong chat id. |
| `dead`: "Gave up after N attempts" | The sink kept failing for about `max_attempts` backoffs. Fix the receiver; new notifications deliver normally. |
| Telegram rules are not offered in the UI | `GET /notifications/sinks` lists `integration` only for users with an enabled messaging integration. |

## Session credentials

A session's own Forge MCP calls carry a scoped, session-bound credential. With it a
session may read the feed and its owner's sessions (`forge:session:read`) and notify
as itself (`forge:notify`), but it cannot touch delivery rules, sinks or delivery rows.
Session credentials are local to their node. The operator guide covers the scopes,
the grants, the token lifecycle and the Forge-hosted MCP endpoint
(`POST /api/v1/forge/mcp`): [forge-mcp.md](forge-mcp.md).

## Configuration

```yaml
notifications:
  enabled: true              # projection, the API and the SSE event
  reply_ready:
    enabled: false           # true: a reply_ready for every final reply (noisy)
    title_chars: 120         # <= 200 (protocol limit)
    body_chars: 280          # <= 4000 (protocol limit)
  default_page_size: 50
  max_page_size: 200
  public_web_url: ""         # deep-link origin; empty = the host's public origin
  host_label: ""             # host name shown in messages; empty = the URL's hostname
  session_link_path: /volundr/session/{session_id}#notification-{notification_id}
  feed_link_path: /volundr/notifications
  resident_link_path: /ravn?ravn={resident_id}&tab=chat
  resident_session_link_path: /ravn?ravn={resident_id}&tab=chat&session={resident_session_id}
  dispatcher:
    enabled: true            # run the outbox dispatcher on this host
    poll_interval_seconds: 2.0
    batch_size: 50
    lease_seconds: 60
    send_timeout_seconds: 20 # must be shorter than lease_seconds
    max_concurrent_sends: 8
    max_attempts: 8
    backoff_base_seconds: 5
    backoff_max_seconds: 900
    backoff_jitter_ratio: 0.2
    default_rate_limit: {max_count: 60, window_seconds: 3600}   # null = none
    max_error_chars: 2000
  integration_sinks:         # integration slug -> NotificationSink class
    telegram: niuu.adapters.notifications.telegram.TelegramNotificationSink
    webhook: niuu.adapters.notifications.webhook.WebhookNotificationSink
  sinks:                     # dynamic adapters: name, label, adapter, then kwargs
    - name: ops-webhook
      label: Ops webhook
      adapter: niuu.adapters.notifications.webhook.WebhookNotificationSink
      url: https://hooks.example.com/forge
      secret_kwargs_env: {secret: FORGE_OPS_WEBHOOK_SECRET}
```

Sink names use lowercase letters, digits, `.`, `_` and `-`. `integration` is reserved.
Every key of a sink entry except `label`, `adapter` and `secret_kwargs_env` is passed to
the adapter. `secret_kwargs_env` maps a kwarg to the environment variable that holds its
value. A sink that cannot be built stops Forge at startup, naming the entry. In Helm, set
`notifications:` in `values.yaml` (it is rendered verbatim into the Volundr config) and
mount sink secrets with `extraEnv`. `GET /api/v1/forge/feature-flags` reports
`notifications_enabled` and `capabilities.notifications`.

The shared sink port is `niuu.ports.notifications.NotificationSink`. A custom sink
implements `name` and `async send(OutboundNotification) -> DeliveryResult`, and raises
`NotificationDeliveryError(retryable=…)` on failure. It can then be named in
`notifications.sinks`, or stored as the adapter of a messaging integration whose slug is
not in `integration_sinks`.

## Storage

Migration `000084_forge_notifications` creates four tables:

- `forge_notifications`, indexed on `(owner_id, seq)`, `(session_id, seq)`,
  `(project_id, seq)` and `(kind, seq)`;
- `forge_notification_read_states`, one watermark per reader;
- `forge_notification_rules`;
- `forge_notification_deliveries`, which cascades from both its notification and its
  rule, and is indexed on `(status, next_attempt_at)`.

Migration `000085_forge_notification_delivery_rate_index` adds a partial index on
`(rule_id, delivered_at) WHERE status = 'delivered'` for the per-rule rate limit.

Both migrations live in `migrations/`; the Helm migrations configmap and the CLI bundle
read them from there. Mini mode applies them at startup.

To check them, and the dispatcher against a local webhook receiver, on a disposable
PostgreSQL database, run:
`FORGE_HISTORY_TEST_DATABASE_URL=postgresql://… pytest -m integration tests/test_adapters/test_notifications_postgres_integration.py tests/test_adapters/test_notification_dispatcher_postgres_integration.py`.
The `database` lane of `scripts/verify_forge.py` includes both.


## Individual activity reads and durable reply anchors (local follow-up)

`PUT /api/v1/forge/notifications/{id}/read?instance_id=<host>` acknowledges only that
card for the caller. No request body. The returned `id`, `instance_id`, `read: true`
and `read_state` confirm the exact write; other cards remain unread. Repeated PUTs
are idempotent. The legacy read-through API remains the explicit Mark all read action.
Missing/unauthorized items are404; unsupported hosts must not trigger a read-through
substitute. Feed/session lists, unread filters and counts all include these marks.

Migration000071 adds the reader/item table and is mirrored in the Helm migration
configmap. It is additive; no historical notifications or watermarks are rewritten.
Check the migration number against the target deployment before integrating. Rollback
to older binaries does not delete marks, but older clients/servers cannot display them;
the down migration deletes item acknowledgements and is not a routine binary rollback.

Reply anchors can name a durable source turn whose displayed row was split/re-IDed
by observation-order presentation. Updated projections record source identity, and the
turn resolver returns the unique terminal fragment plus `requested_turn_id` proof.
Both Forge and the owning Skuld gateway need the updated code. Already-running old
gateways and old projected-only archives without source provenance may still return404;
never infer an anchor from a title or timestamp. Use a separately authorized rollout
and recovery window; do not restart active sessions just to refresh this code.
