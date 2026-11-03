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
| `system` | attention | A resident has a question waiting in the ODIN review queue (`answer_operator_question`). Read from the queue through `niuu-ravn`; see [Resident questions in the ODIN review queue](#resident-questions-in-the-odin-review-queue). | `odin-review:{owner}:{review_id}` (backlog digest: `odin-review-digest:{owner}`) |
| `system` | attention | A Work review gate is waiting for a person. Forge projects it from the gate's `workflow_gate` frame when the log batch is appended; see [Review gates](#review-gates). | `workflow-gate:{session}:{gate_id}` |

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
| `GET /notifications/{id}/image[?variant=thumb]` | The notification's stored image or its thumbnail, for whoever can read the notification. `404` when none is stored. See [Images](#images). |

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

## Resident questions in the ODIN review queue

Every question a resident asks its owner is also filed in the ODIN review queue that
`niuu-ravn` serves (`/api/v1/ravn/odin/reviews`, item `requested_action:
answer_operator_question`). The queue notifies nobody, and it also holds the residents'
morning briefs and action drafts. The ODIN review relay (`notifications.odin_review_relay`,
off by default) follows it from Forge and turns each **pending question** into a
notification for the configured owners, through the same feed, rules and push sink as any
other. It reaches residents Forge did not create (those deployed by Fleet), which the
[resident outbox relay](#residents) cannot see.

- **One per question per owner.** The notification is `attention` / `system` /
  `warning` with no `session_id` and **no** `resident_id` (the ODIN ids are not Forge
  resident-runtime ids). Title `"<valkyrie_id> asks"`, body the question. Recording is
  idempotent (dedupe key above), so a poll, a restart or a second replica never makes a
  second one.
- **Where to answer.** The feed item carries `odin_review_id` and
  `odin_review_decide_path` (`/api/v1/ravn/odin/reviews/<url-quoted id>/decide`, on the
  host serving the queue). `POST` it `{"decision": "answered", "answer": "<text>"}`: the
  resident receives the text verbatim. Notification metadata also holds `valkyrie_id`,
  `environment_id` and `input_kind: "question"`.
- **Push.** A question pushes through the owner's **Needs-input pushes** rule (needs
  `push.enabled`). Its APNs payload carries `aps.category = NIUU_RESIDENT_QUESTION`, and
  the custom keys `notification_id`, `odin_review_id` and `odin_review_decide_path`, so the
  phone offers an inline Reply. Any notification can ask for this by setting
  `push_category` and `push_data` in its metadata; pushes that do not are unchanged.
- **Retired when settled.** Once the item leaves `pending` (answered, approved, rejected,
  expired), the owner's notification is marked read. The relay asks the item itself before
  retiring, so a listing that missed it never retires a question that is still waiting.
- **Backlog.** The first time the relay follows the queue for an owner it stores a
  watermark. The questions already pending then (requested at or before it) are recorded
  with `feed_only` metadata: they are in the feed but **no rule schedules a delivery for
  them**, so nothing is pushed one by one. One digest notification ("N resident questions
  waiting", body "Waiting on you: muninn (3), regin (2)") does push. Questions asked after
  the watermark push individually. The watermark and the items announced live in
  `odin_review_relay_owners` / `odin_review_relay_items`, shared by every replica.
- **Failures are loud.** A failed poll logs at error level and counts on
  `app.state.odin_review_relay.stats` (`failures`, `consecutive_failures`, `last_error`,
  `last_success_at`, `malformed`); the next interval retries it. A pending question item
  with no readable question or timestamp is logged, counted as `malformed`, and not
  announced. Enabled but unusable configuration stops Forge at startup.

```yaml
notifications:
  odin_review_relay:
    enabled: true
    owner_ids: ["<Forge user id>"]   # whose feed receives the questions
    poll_interval_seconds: 30        # the queue has no change stream
    source:                          # dynamic adapter + kwargs
      adapter: volundr.adapters.outbound.http_odin_review_source.HttpOdinReviewSource
      kwargs:
        base_url: https://yggdrasil.niuu.world
        auth_adapter: niuu.adapters.outbound.http_auth.WorkloadIdentityBearerTokenAuthAdapter
        auth_kwargs:
          exchange_url: https://yggdrasil.niuu.world/api/v1/tokens/workload/exchange
          token_file: /var/run/secrets/niuu-workload/token
          audiences: [niuu-ravn]
```

The source authenticates with an `HttpAuthPort` adapter, the same ones Ting and Observatory
use for service-to-service calls; Forge creates no token. In the cluster, project a
ServiceAccount token with the exchange's audience into the Forge pod (`extraVolumes` /
`extraVolumeMounts`), and give the exchange a `workloadIdentity.mappings` entry for Forge's
ServiceAccount; `niuu-ravn`'s gateway then accepts the exchanged workload JWT.

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
notification. Image URLs on items the facade returns carry `instance_id=<host>`, and
`GET /notifications/{id}/image?instance_id=<host>` is proxied to that host (the default
host without it).

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

## Questions and runtime requests

An agent asks the user something with `notify`'s `question: true`, optionally with a
structured `ask: {items: [...]}` (questions with options, or actions the user reports as
done), and refers to an earlier question with `about_question: {id, action}`. Forge
stores these in the notification's metadata and serves them unchanged on the feed and
the live event; the user's answer is an ordinary message to the session. A system
`attention` item instead carries `input_request: {kind, request_id}`: the runtime request
(AskUserQuestion, confirmation, permission) the session is blocked on, answered in the
session's question card. Field rules: `docs/forge/session-notifications-contract.md` §12.

## Review gates

A Work workflow stops at a review gate until someone decides it. The session's broker
writes the gate's state to the session log as a `workflow_gate` frame when the gate
opens, and again when it is decided. Forge projects those frames, the same way it
projects notification turns, so a gate needs no relay and no Work configuration.

- **When a gate opens.** Forge records one `attention` / `warning` / `system` item
  anchored to that log row (`session_seq`). It carries the session's
  `coordination.project_id` in `project_id`, so project rules and
  `GET /notifications?project_id=` select it, and the owner's "Needs-input pushes" rule
  pushes it to their phone like a session's question. The title is the session name and
  the gate's label. The body is the gate's summary, or its instructions when it has
  none.
- **Routing to the gate.** The item's `input_request` is
  `{kind: "workflow_gate", request_id: <gate id>}`. A client decides it with
  `POST /sessions/{session_id}/workflow/gates/{request_id}/resolve`, percent-encoding
  the gate id, which contains colons. That route requires the header
  `x-niuu-workflow-gate-intent: resolve`.
- **When a gate is decided.** The decided frame settles the item, wherever the decision
  was made: the Work page, the session page, the phone or an agent allowed to answer the
  gate. The item is marked read and resolved, with `resolution` set to the gate's
  outcome (for example "Review the result approved by Ada."), and is sent again as
  `session_notification` so clients replace it in place.
  `resolved_by_notification_id` stays null.
- **Which gates notify.** Every gate that waits for a person. A gate in
  `automated_approval` mode (an agent answers it) and a gate with
  `pendingBehavior: silent` raise nothing. Evidence gates log no gate frame.

**One item per opening.** The gate's id names its attempt, so a retried log append, or a
Forge or session restart, keeps one item. A gate sent back with requested changes and
opened again is a new attempt, and asks again. Only a decision settles the item: a
session that finishes a turn or stops waiting on a question leaves it open.

**What does not settle an item.** A session that stops or is deleted while its gate is
pending decides nothing, so the item stays open. A restarted session restores its gate,
which can still be decided. Resolve an abandoned item by hand
(`PUT /notifications/{id}/resolve`). A gate that opened before this version has no item.

An unreadable `workflow_gate` frame raises no item and never holds back the rest of its
log batch, because no retry could change it. Forge logs it at error level with the
session and seq and counts it on `volundr.notifications.workflow_gate_frame_malformed`.

## Images

An agent attaches an image with `notify`'s `image` input: `{"file_id": …}` from
`present-file`, or `{"url": "https://…"}`, plus `alt`. Forge records the notification
immediately with `image_state: pending`, then copies the image once in the background
(the image worker) and stores the original plus a thumbnail in
`forge_notification_images`:

| `image_state` | Meaning | Item |
|---|---|---|
| `pending` | Forge has not copied it yet. | `image: null` |
| `ready` | Stored. | `image: {url, thumbnail_url, mime, width, height, alt}` |
| `failed` | The copy was refused or failed; the reason is in `image_error`. Not retried. | `image: null` |
| `null` | No image was attached. | `image: null` |

The live `session_notification` for a notification with an image is sent once its
image settles, so it carries the same `image` object as the feed.

A `file_id` image is read from the session's broker, so the session must still be
running when the worker copies it (normally within a second of the notify). A `url`
image must be https on every redirect hop and, by default, resolve only to public
addresses; set `notifications.images.allow_private_networks: true` to allow private
or tailnet hosts. Types: PNG, JPEG, GIF, WebP; at most 10 MiB and 40 megapixels.

| `image_error` | Likely cause |
|---|---|
| "session … is not running" / "no longer exists" | The session stopped before the copy; re-send with a `url` or present the file again. |
| "the session broker answered HTTP 404/410" | Unknown or expired present-file id. |
| "… sent text/html, not an accepted image type" | The URL is a web page, not an image. |
| "refusing …: it resolves to a non-public address" | A private or tailnet host; see `allow_private_networks`. |
| "… larger than 10485760 bytes" / "… more than 40000000 pixels" | Too large; send a smaller rendering. |
| "fetching the image timed out after …" | Slow source; see `images.fetch_timeout_seconds`. |

Pending copies left by a restart are claimed again after their lease. The worker logs
under `volundr.domain.services.notification_images`.

## Speak intent

An agent may ask for a notification to be spoken aloud on the owner's phone with
`notify`'s `speak` input: `{"text": "…", "priority": "now" | "next_window"}`. `text`
is what is said (trimmed, 1–600 characters); `priority` defaults to `now`. Any agent
kind may carry it, never `reply_ready`. An invalid intent is refused (MCP tool error,
HTTP 422) and nothing is recorded.

Forge only stores the intent and returns it as `speak` on every item (the feed, the
per-session list and the live `session_notification` event; `null` when absent). It
does not deliver or speak anything: the Lexi agent service reads the feed and decides
whether and when the phone speaks. The web shows a "Spoken" badge on such items.
Field rules: `docs/forge/session-notifications-contract.md` §13.

## Resolving notifications (LEX-1442)

An alarm should stop alarming once its incident is over. A notification carries
`resolved` (bool), `resolved_at`, `resolved_by_notification_id` (the notification that
closed it; null when no notification did) and `resolution` (the operator's text, or
what the gate's decision was). A notification is resolved at most once: the first
resolution is kept.

Deciding a review gate resolves that gate's item; see [Review gates](#review-gates). A
person or an agent can also resolve one in three ways:

- **By incident.** Give every notification about one incident the same `correlation_id`
  (a ticket or PR number: one incident = one id). A later `milestone` with that id
  resolves the owner's earlier **attention** and **error** notifications, and anything
  **critical**, for that id. Earlier info, decision and milestone items stay as they
  are.
- **Explicitly.** `notify` (and `POST /sessions/{id}/notifications`) accept
  `resolves: [...]`, up to 20 refs:
  - a notification id resolves that notification;
  - any other string is a correlation id, and resolves every earlier notification of
    that incident, of any kind.

  Only the same owner's notifications are touched. The refs are served back as
  `resolves`.
- **By hand.** `PUT /api/v1/forge/notifications/{id}/resolve` with an optional
  `{"resolution": "…"}`. It is idempotent, and on a Guild host it is forwarded to the
  node that holds the notification. Session credentials are refused (403); a session
  resolves through `resolves` instead.

**Reading:**

- `GET /notifications?unresolved=true` returns only open notifications, and
  `unresolved=false` only resolved ones. Without the parameter the feed is unchanged.
- The Forge MCP `list_notifications` defaults to `unresolved=true`: an agent checking
  what is still open does not see finished incidents. Pass `unresolved=false` or `null`
  to see them.
- When a notification is resolved, Forge sends it again as `session_notification`
  with the resolved fields, so clients replace it in place.

**Not done in Forge (other lanes):**

- collapsing resolved items under their milestone in the apps;
- the control plane's Needs-you counts and the voice summaries;

**Stale.** An unresolved attention, error or critical notification is served with
`stale: true` when both of these hold:

- it is older than `notifications.stale_after_seconds` (4 hours by default);
- its session has posted a newer milestone.

The session moved on, so the alarm is probably over even though nothing resolved it.
The flag is computed when the feed is read, with one grouped query per page, and never
stored. It resolves and hides nothing: clients grey the item out, and voice summaries
say "stale, from 05:12".

Storage: migration `000098_forge_notification_resolved`. It adds three nullable
columns, which rewrites nothing, and a partial index on `(owner_id, correlation_id)`
for unresolved rows.

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

This is the per-node outbox sink. For the iPhone app (alerts for every Guild node, Live
Activity and widgets) use the push gateway instead: see
[Apple push](#apple-push-the-push-gateway).

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
(`POST /api/v1/forge/devices`; on a Guild this registers the device on every visible
node, and fails if any node does not accept it, so the app registers again), Forge gives
them a **Needs-input pushes** rule:
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

With the push gateway (`push.gateway.enabled`) the gateway owns Apple push: it alerts for
every notification of the Guild, the needs-input moment included, so no "Needs-input
pushes" rule is given and `push.adapter` stays at its default. A rule an owner points at a
push sink would alert a second time for the same notification. The gateway needs
`push.device_registry: postgres` (or `auto` in mini mode): token kinds and push throttles
live in the `device_tokens` table, and the push-device integrations keep alert tokens only
(registering another kind there answers 422).

APNs results: delivered if at least one device accepted. It is retryable when every
device failed transiently (429/5xx, network). It is `dead` when the owner has no iOS
alert tokens or APNs rejected them all. The channel (`ApnsNotificationChannel`) reads its
key from `private_key_path` (the `.p8` file) or, as PEM text, from `private_key`
(`secret_kwargs_env`); it sends only to `alert` tokens, each on the APNs host of its
`environment`.

### Deep links

Messages link back to `notifications.public_web_url`, or else the host's public origin,
with `session_link_path` (default
`/volundr/session/{session_id}#notification-{notification_id}`, which opens the session
at the notification's card) or `feed_link_path` for sessionless notifications. Set
`public_web_url` whenever Forge is reached through a proxy or a public hostname. Relative
notification links (`/volundr/…`) are made absolute against the same base.

## Apple push (the push gateway)

With the push gateway, a notification recorded on **any** node of a Guild reaches the
owner's iPhone while the app is closed, in four ways:

- an **alert** that opens the notification in the app (for the kinds that need the user);
- a **Live Activity** update carrying the Guild's newest notifications (or a
  push-to-start when no activity is running, if enabled);
- a **widget** reload;
- a silent **background** push that lets iOS wake the app.

Any Forge server turns this on with configuration: its APNs key, key id, team id and the
app's bundle id, and the switch. Nothing in it is specific to one server.

The same gateway alerts for the needs-input moment, from the `attention` notification
every node records, so the per-node needs-input push (`push.adapter`) is not used with it.

### One gateway per Guild

One node, the **gateway**, holds the APNs key and every registered device. It reads every
node's feed through the Guild API on `push.gateway.guild_url` (the node list from the
registry, `GET /api/v1/niuu/instances`, and each node's notifications from
`GET /api/v1/forge/notifications`) and sends the pushes for all of them. The other nodes
need no key, no configuration and no new release: the gateway sees their notifications
the way the app does, already tagged with the node (`instance_id`) the app uses in its
links.

A phone that uses several Forge servers (several Guilds that cannot reach each other)
registers its tokens with each of them, and each server pushes for its own Guild. The
pushes are built for that:

- a Live Activity push replaces the whole card, and a server only knows its own Guild, so
  the content names the server (`origin`) and the app merges every server the next time
  it runs;
- the background push is what lets iOS run the app for that merge;
- the widget reads every server itself when it reloads, so any server's reload is enough.

How a notification becomes a push:

1. The gateway keeps one position (`seq`) per node. It reads the feed after those
   positions and stores each new notification as a pending alert, together with the
   advanced positions, in one transaction. A restart or an unreachable node therefore
   loses nothing. It reads the feed every `poll_interval_seconds` (5 s), and at once
   when its own node records a notification, so a notification of the gateway's node
   is pushed immediately and another node's within the poll interval.
2. Each pending alert is settled once: `sent`, `suppressed` (the alert rule below),
   `skipped` (the owner has no alert token) or `failed`. An alert APNs could not take
   (unreachable, 429, 5xx) is retried with backoff up to `max_attempts`.
3. The owner's Live Activity and widget tokens are pushed, and the app is sent a
   background push, for **every** new notification, whatever its kind. Reading a
   notification pushes nothing, so the Live Activity's top row is always the newest
   event.

Nodes are read one by one, at the same time. A node that does not answer within
`request_timeout_seconds` (asleep, restarting) fails only its own read: the gateway keeps pushing for the others, leaves that node alone for
`node_retry_seconds`, then tries again and catches up from its stored position. While a
node is away the Live Activity shows the newest notifications of the nodes that answer.
`GET /push/status` lists such nodes under `unreachable_nodes`, with the reason, and counts
each failed read in `node_failures`; the log says it once when it starts and once when the
node is back.

A node running an older release without the notification feed is not an outage. The Guild
names it in the `X-Forge-Feedless-Instances` header (only a 404 for the missing route
counts; any other 404 is an ordinary failure). The gateway lists it under `feedless_nodes`,
logs it once at info, and asks it again only every `feedless_recheck_seconds` (each recheck
counts in `feedless_rechecks`). The Guild facade remembers such a node for
`guild_feedless_recheck_seconds` too, and keeps its cursor position, so its notifications
are read once it is upgraded.

With `stream_enabled`, the gateway also listens to the Guild's live event stream and
pushes another node's notification at once. That holds one streaming connection to
`guild_url`. Leave it off when the Guild facade runs in the gateway's own process (the
single-process host): an open stream to itself keeps the server from shutting down
gracefully until the service's stop timeout.

A node the gateway has not read before starts `catch_up_items` before its newest
notification; older history is never replayed, and `alerts.max_age_seconds` keeps a
stale notification from alerting after an outage.

The gateway reads the Guild as **one identity**. Without `reader_token_path` it sends no
credential, which suits a Guild running the AllowAll identity (every request is the same
user). With real authentication, put a personal access token of the user in that file.
The unread flags and count in the Live Activity are that reader's, so the gateway serves
the Guild of one user; several human users would each need their own reader, which is
not built.

`guild_url` must be a Guild API origin. A single Forge node without the Guild facade has
no registry and is refused with a clear error.

### Registering a device

The app registers each token with `POST /api/v1/forge/devices`, on the Guild facade or on
the gateway node directly, with the caller's normal credentials:

```json
{
  "platform": "ios",
  "token": "<hex token>",
  "kind": "alert | live_activity | live_activity_start | widget",
  "environment": "sandbox | production",
  "app_bundle_id": "com.example.app",
  "activity_id": "<Live Activity id, for live_activity only>"
}
```

- One registration per `(token, kind)`. Registering again refreshes it; a token another
  user registered before moves to the caller.
- `kind`: `alert` is the app's device token; `live_activity` is the update token of one
  running Live Activity (`activity_id` names it, and a newer token for the same activity
  replaces the earlier one); `live_activity_start` is the push-to-start token; `widget`
  is the widget extension's token. Omitted, it is `alert`.
- `environment` selects the APNs host for that token, per registration.
  - A development-signed install holds **sandbox** tokens; TestFlight and App Store
    installs hold **production** tokens. The app derives it from its signing (LEX-1734).
  - `development`, the entitlement's own word, also means sandbox.
  - Omitted or unknown, the registration is still stored, as `production`, with a warning
    in the log. The gateway's status counts it (`registrations_environment_missing`,
    `registrations_environment_unknown`), so a build that forgets it is visible.
  - A token sent to the wrong host is refused by APNs as `BadDeviceToken` and deleted.
- `app_bundle_id` is the APNs topic base; without it the gateway uses `push.apns.bundle_id`.
- `201` with the stored registration. `503` when the node sends no pushes
  (`push.enabled` is off), so a client can tell the token is not in use there.

`GET /api/v1/forge/devices` lists the caller's registrations and
`DELETE /api/v1/forge/devices/{token}` removes them (`?kind=` removes one kind). The Guild
facade forwards all three to its default node, or to the node `instance_id` selects.
Session credentials (an agent's own token) cannot use these routes.

A token APNs reports as dead is deleted: HTTP `410`, or the reasons `BadDeviceToken`,
`Unregistered` and `ExpiredToken`. The app registers again on its next launch.

### What is pushed

**Alert** (`apns-push-type: alert`, topic = the bundle id, `apns-collapse-id` = the
notification id, `apns-expiration` = now + `expiration_seconds`):

```json
{
  "aps": {
    "alert": {"title": "<title>", "subtitle": "<session name>", "body": "<body, shortened>"},
    "interruption-level": "time-sensitive | active",
    "sound": "default",
    "category": "FORGE_ACTIVITY",
    "thread-id": "<session id>",
    "mutable-content": 1
  },
  "deepLink": "lexichat://activity/<notification id>?inst=<instance id>&session=<session id>&turn=<turn id>",
  "notification_id": "<notification id>",
  "instance_id": "<instance id>",
  "session_id": "<session id>",
  "kind": "<kind>",
  "image_url": "<absolute URL>",
  "image_thumbnail_url": "<absolute URL>"
}
```

`deepLink` carries only the query items that exist, and the id is percent-encoded.
`subtitle` is the session name when the title does not already contain it.
`interruption-level` is `time-sensitive` only for `attention_kinds` (attention, decision
and error by default: the kinds that need the user; a failure that stops work breaks
through like a question) and `active` otherwise. An attention
push also lets the app's notification service extension show the agent as the sender
(LEX-1186). It carries:

- `aps.mutable-content: 1` and `attention: true`;
- `agent_id`, the session id;
- `agent_name`, the session name;
- `conversation_id`, `<instance_id>:<session_id>`;
- `engine`, the session's engine family in lowercase (`claude`, `codex`, …), when the
  feed reports one; the app picks the avatar from it.

The agent keys are absent when the notification has no session; other kinds carry none
of these keys. `thread-id` and `session_id` are absent for a notification without a
session. `image_url` and `image_thumbnail_url` (with `mutable-content`) appear when the
notification has an image and `public_api_url` is set; the URLs are
the image route on that origin, routed to the node that holds the image. The alert does
not wait for the node to copy the image: while the copy is in progress the URLs are
already named, and the route answers 404 until the copy is stored (or for good, when the
copy fails), so a client fetching the image treats 404 as "no image".

**Live Activity** (`apns-push-type: liveactivity`, topic `<bundle id>.push-type.liveactivity`,
priority 10), sent to every `live_activity` token of the owner:

```json
{
  "aps": {
    "timestamp": 1790923214,
    "event": "update",
    "stale-date": 1790952014,
    "content-state": {
      "items": [
        {
          "id": "<notification id>", "kind": "decision", "title": "<at most 90 characters>",
          "sessionName": "<at most 40 characters>", "instanceID": "<instance id>",
          "sessionID": "<session id>", "turnID": "<turn id>",
          "createdAt": 812616014.0, "unread": true
        }
      ],
      "unreadCount": 1,
      "updatedAt": 812616016.0,
      "origin": "<origin_label>"
    }
  }
}
```

`content-state` matches the app's `ForgeNotificationsActivityContent`: at most three
items, newest first, from the Guild's merged feed; absent values are left out. Dates are
what Swift's `JSONDecoder` reads by default: a number of seconds since
2001-01-01T00:00:00Z (Unix seconds minus 978307200). `stale-date` is now +
`stale_seconds`. `origin` is `origin_label`, left out when empty. Titles and session names
are cut to the app's limits by code point.

With `live_activity.start.enabled`, an owner with a `live_activity_start` token and no
running activity (no `live_activity` token, or APNs reported them all dead) gets
`"event": "start"` instead, with `"attributes-type": "ForgeNotificationsActivityAttributes"`,
`"attributes": {}` and `"alert": {"title": <notification title>, "body": <session name>}`,
at most once per `start.min_interval_seconds` per token. It is off by default: the app
lets the user pin the activity, and a push-to-start brings it back after a dismissal.

**Widgets** (`apns-push-type: widgets`, topic `<bundle id>.push-type.widgets`, priority 10):
`{"aps": {"content-changed": true}}`, at most once per `widgets.min_interval_seconds`
(2 minutes) per token (iOS budgets these). A reload held back by the interval is sent
when the interval ends, so the last change is never dropped.

**Background** (`apns-push-type: background`, topic = the bundle id, priority 5), sent to
every `alert` token: `{"aps": {"content-available": 1}, "forge": {"reason": "notification"}}`,
at most once per `background.min_interval_seconds` (15 minutes) per token, with the same
rule for one held back. iOS decides whether and when it wakes the app.

### Which notifications alert

The gateway has one alert rule, in configuration (`push.gateway.alerts`), applied per
owner. Nobody creates a delivery rule by hand: a registered alert token is the opt-in.

- `kinds`: milestone, decision, attention and error by default. `info` and `reply_ready`
  do not alert unless listed; they still update the Live Activity and the widgets.
- `min_severity` and `sources` narrow it further.
- `rate_limit`: at most `max_count` alerts per owner in any `window_seconds` (30 per hour
  by default; `null` disables).
- `quiet_hours`: `start`, `end`, `timezone` and `allow_min_severity`, with the meaning
  they have on a delivery rule: inside the window only notifications at or above
  `allow_min_severity` alert.
- `max_age_seconds`: a notification older than this when the gateway reads it is not
  alerted. One the reader already read is not alerted either.

These alerts do not go through the delivery outbox, so owner rules
(`/notifications/rules`) and `GET /notifications/{id}/deliveries` do not apply to them.
Do not also select the `push` sink below with an APNs channel for the same owner: a
notification of the gateway's own node would then alert twice.

### Setting it up

1. In the Apple Developer portal, create an APNs auth key (`.p8`). Note its key id and
   the team id. Put the file on the gateway node, readable only by the Forge user.
2. Add the `push:` section (see [Configuration](#configuration)) with `push.enabled`,
   `push.apns` and `push.gateway`, and restart Forge. Migration `000099_push_gateway`
   is applied at start.
3. Start is refused, with the setting to fix, when the key file is missing, unreadable
   or not an EC P-256 key, when the HTTP/2 library (`h2`) is missing, or when
   `guild_url` is not an http(s) origin.

To check it:

```bash
# The gateway is running, reads every node and counts what it did.
curl -s http://<host>:8080/api/v1/forge/push/status

# After the app registered: push once to your own registered tokens. Throttles do not apply.
#   kind: alert | background | live_activity | live_activity_start | widget
#   token (optional): only that registered token; omitted pushes to every token for the kind
curl -s -X POST http://<host>:8080/api/v1/forge/push/test \
  -H 'content-type: application/json' \
  -d '{"kind": "alert", "title": "Forge test push", "body": "It works"}'
curl -s -X POST http://<host>:8080/api/v1/forge/push/test \
  -H 'content-type: application/json' -d '{"kind": "widget", "token": "<hex token>"}'
```

`alert` shows the given title and body and opens the app's activity list. `background` is
the silent push, sent to the alert tokens. `live_activity` and `live_activity_start` carry
the caller's real current notifications. `widget` reloads the widgets. The answer lists
each token (its last 8 characters, its environment) with APNs' verdict: `accepted`, or
the HTTP status and reason. `404` means the caller has no registered token for that kind,
or that `token` is not one of them. Both routes exist only on the gateway node and
through the Guild facade.

| Symptom | Likely cause |
|---|---|
| `POST /devices` answers 404 through the Guild | The Guild facade is older than this release. |
| `POST /devices` answers 503 | `push.enabled` is off on that node. |
| `status` shows `last_error`: "The Guild feed is unreachable" | `guild_url` is wrong, or the Guild is not up yet (the gateway reads again every `poll_interval_seconds`). One such warning in the log right after a start is normal when the Guild runs in the same process: the gateway starts before the server accepts connections. |
| `status` shows `last_error`: "… answered HTTP 401/403" | The Guild needs a credential: set `reader_token_path`. |
| `unreachable_nodes` names a node | It does not answer its feed read (the reason is given): asleep or restarting. Its notifications are caught up once it answers. |
| `feedless_nodes` names a node | It runs a release without the notification feed (Spark today). Nothing to push from it; it is asked again every `feedless_recheck_seconds`. |
| `nodes` lacks a node | It has not answered since the gateway started, or it is not an enabled Forge instance in the Guild registry. A new node is found within `discovery_interval_seconds`. |
| Test push: `HTTP 403 InvalidProviderToken` | Wrong `team_id` or `key_id`, or the key was revoked. |
| Test push: `HTTP 400 BadDeviceToken` (the token is then deleted) | The token's `environment` is wrong: a development install registered as production, or the reverse. |
| Test push: `HTTP 400 DeviceTokenNotForTopic` / `TopicDisallowed` | `bundle_id` (or the registration's `app_bundle_id`) is not the app's, or the key does not cover it. |
| Background pushes are `accepted` but the app does not wake | iOS decides: it delivers only a few per hour, and none to an app the user force-quit or with Background App Refresh off. |
| Alerts arrive but no Live Activity update | No `live_activity` token is registered (the activity is not running), and push-to-start is off. |
| An alert row is `suppressed` | The reason is in its `detail` (`forge_push_deliveries`): kind, severity, source, age, quiet hours or rate limit. |

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
  images:                    # copying images attached to notifications
    poll_interval_seconds: 5.0     # recovery poll; a new image wakes the worker at once
    batch_size: 8
    lease_seconds: 60
    fetch_timeout_seconds: 20      # must be shorter than lease_seconds
    max_concurrent_fetches: 4
    max_redirects: 3               # url images; every hop is checked
    allow_private_networks: false  # url images may resolve to private/tailnet addresses
    thumbnail_max_side: 320
    thumbnail_jpeg_quality: 85
    cache_max_age_seconds: 31536000  # Cache-Control on served images (bytes never change)
    max_error_chars: 300
  integration_sinks:         # integration slug -> NotificationSink class
    telegram: niuu.adapters.notifications.telegram.TelegramNotificationSink
    webhook: niuu.adapters.notifications.webhook.WebhookNotificationSink
  odin_review_relay:         # resident questions from the ODIN review queue (see above)
    enabled: false
    owner_ids: []
    poll_interval_seconds: 30
    source: {adapter: volundr.adapters.outbound.http_odin_review_source.HttpOdinReviewSource, kwargs: {}}
  sinks:                     # dynamic adapters: name, label, adapter, then kwargs
    - name: ops-webhook
      label: Ops webhook
      adapter: niuu.adapters.notifications.webhook.WebhookNotificationSink
      url: https://hooks.example.com/forge
      secret_kwargs_env: {secret: FORGE_OPS_WEBHOOK_SECRET}
```

The push gateway has its own section. The defaults are shown; `enabled`, `apns` and
`gateway.enabled` / `guild_url` are what an operator sets:

```yaml
push:
  enabled: true              # push on: the gateway below, or else the per-node adapter
  apns:
    team_id: ""              # Apple Developer team id
    key_id: ""               # id of the APNs auth key
    bundle_id: ""            # the app's bundle id (topic for a token without one)
    private_key_path: ""     # the AuthKey_<KEYID>.p8 file; never its contents
    token_ttl_seconds: 3000  # provider JWT reuse (20 to 59 minutes)
    request_timeout_seconds: 10
    production_url: https://api.push.apple.com
    sandbox_url: https://api.sandbox.push.apple.com
  gateway:
    enabled: false           # true on the one node that pushes for the Guild
    guild_url: ""            # Guild API origin, e.g. http://127.0.0.1:8080
    reader_token_path: ""    # bearer token file; empty for an AllowAll Guild
    public_api_url: ""       # origin a phone reaches, for image URLs; empty = no image
    origin_label: ""         # this server's name on a Live Activity it pushed
    request_timeout_seconds: 5     # one read of one node
    node_retry_seconds: 30         # a node that did not answer is left alone this long
    feedless_recheck_seconds: 3600 # a node without the feed is asked again this often
    poll_interval_seconds: 5 # how often the Guild feed is read
    stream_enabled: false    # also listen to the Guild's live events (see above)
    stream_retry_seconds: 5
    page_size: 50            # notifications read per node per request
    feed_page_size: 20       # newest notifications read per node for the Live Activity
    catch_up_items: 10       # read back on a newly found node
    discovery_interval_seconds: 300
    retention_seconds: 604800      # settled alert rows kept
    max_detail_chars: 500
    alerts:
      enabled: true
      kinds: [milestone, decision, attention, error]
      min_severity: info
      sources: []                  # empty = any
      attention_kinds: [attention, decision, error]   # time-sensitive + marked for the app's NSE
      low_priority_kinds: []       # apns-priority 5
      body_chars: 280
      expiration_seconds: 3600
      max_age_seconds: 900
      rate_limit: {max_count: 30, window_seconds: 3600}   # null = none
      quiet_hours: null            # {start: "23:00", end: "07:00", timezone: Europe/Paris,
                                   #  allow_min_severity: critical}
      category: FORGE_ACTIVITY
      sound: default               # empty = silent
      deep_link_base: lexichat://activity
      session_subtitle: true
      batch_size: 20
      lease_seconds: 60
      max_attempts: 5
      backoff_base_seconds: 15
      backoff_max_seconds: 600
    live_activity:
      enabled: true
      priority: 10
      stale_seconds: 28800
      start: {enabled: false, min_interval_seconds: 3600}
    widgets:
      enabled: true
      min_interval_seconds: 120    # a reload held back is sent when it ends
      priority: 10
    background:
      enabled: true
      min_interval_seconds: 900    # one held back is sent when it ends
      priority: 5                  # APNs refuses 10 for a background push
```

`push.gateway.enabled` needs `push.enabled`, every `push.apns` id and the key path, and
`guild_url`; it refuses a customised `push.adapter` / `push.kwargs`, which configure the
per-node needs-input push it replaces. In Helm, set `push:` in `values.yaml` (rendered
verbatim) and mount the key file from a Secret.

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

Migration `000089_odin_review_relay` creates `odin_review_relay_owners` (one backlog
watermark and digest flag per owner) and `odin_review_relay_items` (the review items an
owner has a notification for, and when it was retired).

Migration `000093_forge_notification_images` adds `forge_notification_images`: one row
per notification with an image (cascading from it), holding the reference, the copy
state (`pending`/`ready`/`failed`, with a lease and attempt fence), and once stored the
original bytes, the thumbnail, their types and sizes. A partial index covers pending rows.
Thor applied the same SQL as `000072_forge_notification_images`, and later fork releases
as `000090_forge_notification_images`; `migrations/lineage-aliases.json` adopts both
ledger rows.

Migration `000097_forge_notification_speak` adds `speak_text` and `speak_priority` to
`forge_notifications` (both null, or both set with a check on length, priority and
kind). It is additive: existing rows keep both null.

Migration `000099_push_gateway` serves the push gateway. It extends `device_tokens` with
`kind`, `environment`, `activity_id`, `last_push_at` and `push_due`, and makes a token
unique per `(token, kind)` instead of per owner (existing rows become production alert
tokens; of a token two owners had registered, the newest row is kept). It adds
`forge_push_gateway_state` (the gateway's position per node) and `forge_push_deliveries`
(its alert outbox, one row per `(instance_id, notification_id)`, with partial indexes for
due, sent and settled rows). The same SQL was written as `000073_push_gateway` on the
unmerged `forge/apns-push` branch and as `000096_push_gateway` on fork releases;
`migrations/lineage-aliases.json` adopts a ledger row of either name.

The migrations live in `migrations/`; the Helm migrations configmap and the CLI bundle
read them from there. Mini mode applies them at startup.

To check them, and the dispatcher against a local webhook receiver, on a disposable
PostgreSQL database, run:
`FORGE_HISTORY_TEST_DATABASE_URL=postgresql://… pytest -m integration tests/test_adapters/test_notifications_postgres_integration.py tests/test_adapters/test_notification_dispatcher_postgres_integration.py`.
The `database` lane of `scripts/verify_forge.py` includes both, and the push gateway's
checks: `tests/integration/volundr/test_push_gateway.py` (its stores and the migration,
through the shared integration fixtures and `TEST_DATABASE_PORT=…`) and
`tests/test_adapters/test_push_gateway_postgres_integration.py` (the migration through
the startup runner and concurrent workers, with `FORGE_HISTORY_TEST_DATABASE_URL`).
Never point either at a live database.


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
