# Agent messaging: sessions, rooms and the mesh

The mesh is publish/subscribe first, and it carries two use cases that share the same
mechanics (events in, answers as events, threads):

| | **Events** | **Questions** |
|---|---|---|
| Tool | `mesh_publish` | `mesh_ask` |
| What it is | the publisher announces something: a result, a hand-off, a status | the asker needs something answered |
| Who may send | only on a topic the sender's own persona declares it **produces** | anyone; the asker's `produces` does not limit a question |
| Who receives | everyone who **consumes** (follows) the topic: any number, or nobody | the peers that announce they **produce** the topic, or peers the asker chose by description |
| No receivers | normal: no subscribers is not an error | refused with the reason: a question is never published into silence |
| Answers | events too, from any number of peers or none, grouped by thread; nothing waits | the same; `mesh_ask` can optionally wait |

Direct messages (`send_message`, `ask`, `mesh_send`) are for when an agent knows it needs one
specific peer; they are not the default.

Agents start these conversations themselves. Ting never relays them. The platform stamps who
sent every message, so an agent cannot pass as the user, as another agent or as a room
participant.

## Events: publish and subscribe

```
mesh_publish(topic="review.done", content="PR 12 is approved")
  -> {"event_id": "...", "topic": "review.done", "correlation_id": "<event_id>",
      "status": "published", "request_id": "..."}
```

Publishing is how an agent announces something: a result, a hand-off, a status. The event
goes straight from the session's Skuld onto its mesh: no Forge round trip. The answer is the
transport accepting the event; nothing waits for a consumer and none is promised.
`request_id` makes a retry the same event (the event id derives from the publisher's mesh peer
id, the topic and the `request_id`), and a consumer's inbox deduplicates on it. Putting a
question to the peers who can answer it is a different use case: see "Asking a question".

**Who listens is declared or chosen by the listener, never named by the sender.** A session
listens to the topics its pinned persona lists under `consumes.event_types` (plus
`mesh.consumes_event_types`, default `code.requested`): its standing subscription. It can also
start following a topic or a thread while it works (see "Following topics and threads").
Every session that follows the topic gets the event in its inbox as a peer message from the
publisher's mesh peer, with the event id as the message id. A topic with nobody on it is not
an error the mesh can see: pub/sub cannot tell it from a topic everyone is slow to read.

**Publishing is limited to declared topics.** A session may publish the topics its persona
lists under `produces` (`event_type` and every value of `event_type_map`) and
`mesh.publish_event_types`. Any other topic is refused with 403 naming what the session
declared and the remedy. Refusing rather than allowing is deliberate: a mistyped topic would
otherwise publish to nobody and still answer "published", and `produces` is the contract
workflows wire consumers against and the contract `mesh_ask` resolves producers from. Asking a
question is not limited this way (below), so this limit never stops an agent from asking.

### Answers are events

A consumer answers an event, or a recipient answers a question, by publishing:
`mesh_publish(topic=<a topic it may publish>, in_reply_to=<the message id it received>,
content=...)`. One publication goes to:

1. its topic, for whoever follows it;
2. the **thread's topic**, so everyone following the thread sees the answer, not only the
   asker (see "Threads");
3. the **inbox topic of the peer it answers**, so the asker receives it without having
   subscribed to anything in advance. Every Skuld always listens on its own inbox topic.

Any number of consumers answer, or none. Answers reach the asker's inbox as they come and are
handed to the harness at its turn boundaries, **grouped by thread** in the rendered turn
(`[Thread <id>: 3 messages]`, then each answer, in the order they arrived). An unanswered
question stays silent: no error, no wait, nothing to count. A consumer may also start
something unprompted later, as its own event ("make changes"): a publication with no
`in_reply_to` is a new conversation. `reply(message_id, content)` stays the direct answer, to
the sender only; use it when nobody else should see the answer.

### Threads

Every event belongs to a thread, its `correlation_id`: the one the publisher names, else the
event it answers (`in_reply_to`), else its own id (a new conversation). A message carries it
as `thread`, and the harness sees `[Thread <id>]` in its header when the thread is not just
that message. An event that names a thread or answers one is also published to the thread's
topic (`thread.t<hash of the id>`; hashed so ids that differ only in punctuation never share
a topic). A session follows a thread with `mesh_subscribe(correlation_id=...)`. A consumer
answering an event that named `correlation_id` X echoes X (the header shows it); answering
with only `in_reply_to` lands in the thread of the event it answers.

**Who is the sender.** Skuld stamps the event: the source is this session's mesh peer id,
and a `publisher` block (session id, name, node, peer id) rides in the payload for the
audit trail. Nothing about the sender is taken from the content, and the tool has no
argument for it. The mesh itself does not yet authenticate a source, so a consumer trusts
the event's source as far as it trusts the mesh; sender authentication on the mesh is a
separate change.

**Failures are loud.** A mesh that is not running (503), a transport that refuses the event
(502, naming how many of its destinations it had reached, with the retry remedy: consumers
drop the repeat) and an undeclared topic (403) are tool errors and are logged at error. A
published event increments `skuld.peer.messages` with `channel="mesh"`,
`recipient=<topic>` and `outcome="published"`; a refusal or failure counts as
`outcome="refused"` or `"failed"`. There are no caps on topics, events, answers or fan-out; the
counters and the session status are the visibility. Config: `mesh.publish_event_types`,
`mesh.published_event_urgency` (the attention hint, 0.0-1.0,
default 0.8).

## Asking a question

`mesh_ask` puts a question to the peers that can answer it. It is not a publication: the
question goes to the recipients directly, not to a topic's consumers, and the asker's own
`produces` does not limit it. The asker names the audience one of two ways:

```
mesh_ask(content="did the deploy finish?", producers_of="deploy.status")
mesh_ask(content="is staging healthy?", peers=["source-b"])
```

- **`producers_of`: every peer that announces it produces the topic**, because it is the source
  of that kind of knowledge or work. The platform only resolves this from the peer table as a
  fact lookup (`mesh_peers(produces=<topic>)` shows the same set); whether those are the right
  peers to ask is the asker's judgment, made when it picked the topic. A peer that did not
  announce what it produces is not matched, and is counted in the result
  (`peers_not_announcing_produces`) so the asker knows the match may be incomplete.
- **`peers`: peers the asker chose**, for example by their `description` from `mesh_peers`.
  Every id must be in the peer table; one that is not refuses the whole question by name.

Each recipient gets the question as a peer message from the asker (same message id and thread
for all of them), prefixed with why it was asked ("because you announce that you produce
'deploy.status'" or "because you were chosen to answer"), with the usual header saying who
asked and how to answer. It is acknowledged by each recipient's Skuld like any direct message.

**No recipients is loud.** A question to a topic no peer produces is refused (409) with the
reason, how many peers are known and how many did not announce what they produce; nothing is
sent. This is unlike an event, where no subscribers is normal. A recipient that could not be
reached (gone, timed out, refused) is listed under `failed` with its error and counted on
`skuld.peer.messages` (`outcome="failed"`; `"asked"` for each delivery); the rest still get the
question. If it reached none of them the call is an error naming every failure (502).

**Answers** are events correlated to the question: a recipient answers with `reply` (to the
asker alone) or by publishing with `in_reply_to` set to the question's message id, which also
puts the answer on the thread's topic and in the asker's inbox (see "Answers are events"). Any
number of recipients answer, or none. They reach the asker's inbox as they come, at its turn
boundaries, grouped by thread. Nothing waits by default.

**Waiting is optional.** With `wait_s`, `mesh_ask` also waits (at most `ask.wait_max_s`, the
longest one MCP tool call may run; a larger value is refused and counted as a cap hit) and
returns every answer so far, taken by the call so they are not handed to the harness again.
It ends one of two ways, named in `ended`:

- `all_asked_answered`: every peer the question reached has answered. That set is the asker's
  own choice (the producers it asked, or the peers it named), not a guessed count, so ending
  on it is not a "done" rule. A recipient the question never reached (listed under `failed`) is
  not waited for.
- `deadline`: the time ran out. `not_yet_answered` names who had not answered.

An answer from a peer that was not asked is returned too and never ends the wait early. It does
not return at the first answer, because that would make a partial picture look settled.

**One identity per answerer.** With mesh signing on (`mesh.attestation.required`, the default),
the mesh verifies every sender and tells the inbox the certified peer id and session. An
answer names its sender as a session (`reply`) or as a mesh peer (an event); the verified
identity is what makes those one answerer, so a `reply` and an event from the same peer count
once, in `answered`, in the early stop and in `not_yet_answered`. Each answer carries
`identity` and `identity_kind` (`verified`). The identity is stored with the message in the
durable inbox frame. With signing off nothing is verified: the sender is only claimed,
`identity_kind` is `claimed`, a forged `verified_sender` mark is ignored, and a `reply`
(which names a session, not the peer) cannot be matched to the peer it was asked of, so the
early stop only sees answers that name the peer (events) and a `reply`-only wait runs to the
deadline.

An answer that arrives after the wait is delivered as a message at the next turn boundary.
`ask` keeps its own, different meaning: one session, one answer, with the outcomes it reports.

## Following topics and threads

| Tool | What it does |
|---|---|
| `mesh_subscribe(topic)` | start listening to a topic |
| `mesh_subscribe(correlation_id)` | follow a thread: see every answer published into it |
| `mesh_unsubscribe(topic \| correlation_id)` | stop following something followed at runtime |

The persona's `consumes` stays the standing subscription; a runtime follow adds to it.

- **Durable.** Each change is written to the session's event log (a `mesh_subscription` frame)
  and stored before the call returns, like the peer inbox. A restart replays the log and
  follows again what the session followed, and not what it unsubscribed. A follow the log
  could not store is refused and not started; a session with no durable log cannot follow
  anything. A restored follow the mesh cannot subscribe to fails the start.
- **Announced.** The topics followed at runtime are announced beside the persona's, so peers
  and `mesh_peers` see them. A thread is one conversation and is not announced.
- **Any topic may be followed.** Listening is harmless to everyone else: a session is in one
  realm, where every member already sees every announcement and can be messaged, so listening
  discloses nothing a member cannot learn. What it can do is fill the follower's own inbox,
  which the follower decides, sees (counters, inbox status) and can stop. Only publishing is
  limited to declared topics. Two kinds of name are refused: one that is not a topic, and the
  platform's own `thread.*` and `inbox.*` topics (follow a thread by `correlation_id`; a peer's
  inbox topic carries answers addressed to that peer).
- **Idempotent and loud.** Following what is already followed (including a standing topic)
  answers `already`. Unsubscribing a standing topic, or something not followed, is an error
  that says what is followed. Changes count on `skuld.mesh.follows`.
- **What arrives.** An event is delivered when it carries a prompt (what `mesh_publish`
  sends); events of other shapes on a followed topic are ignored by the inbox.
- **Threads add up.** A followed thread stays followed until unfollowed; there is no expiry
  and no cap.

## Direct messages

Use these when you know you need one specific peer. For a question to whoever is listening,
publish.

### The envelope

Every message from one agent to another travels as a **peer message**
(`niuu.domain.peer_message.PeerMessage`): message id, sender, recipient, send time,
content, a reply route, an optional `in_reply_to`, a `room_id` when it was said in a room,
`relayed_by` when a room host passed it on, `urgent`, and a `thread` (a mesh publication's
correlation id; messages of one thread are rendered together).

The recipient's Skuld shows the harness a header the platform wrote (who sent it, when,
how to answer) and the content. A peer message waits for the end of the recipient's
current turn unless it is `urgent`. A person's chat message is not a peer message and
still interrupts.

| Path | Who stamps the sender | Reply route |
|---|---|---|
| Session MCP `send_message` | Forge, from the caller's `forge_session` credential and session row | the sender's session |
| Cross-node `send_message` | the sender's home node attests it; the owning node builds the envelope | the sender's session on its node |
| Room message to a member session | the hosting session (`relayed_by`, stamped by Forge from its credential); the participant who spoke is the sender | the room, via the host |
| Session MCP `mesh_send` | the sending Skuld: this session and its mesh peer id | the sender's mesh peer (only when `expect_reply`) |
| Session MCP `mesh_publish` | the publishing Skuld: the event's source is this session's mesh peer id | the publisher's mesh peer, always: a consumer may answer later |
| Session MCP `mesh_ask` | the asking Skuld: this session; one message id and thread for every recipient | the asker's mesh peer |

The request body can add `in_reply_to` and `urgent`. A session that sends `sender`,
`recipient`, `relayed_by`, `sent_at` or `message_id` is refused with 400: Forge stamps all
of them. The message id derives from the sender, the recipient and the `request_id`, so a
retry with the same `request_id` carries the same id and is never delivered twice. A person
calling the same route (a PAT or login) keeps the plain user-turn path.

### Send, end your turn, the reply wakes you

Most direct conversations need no waiting. Send the message (`send_message`, or
`mesh_send`), say what you need back, **end your turn**, and do other work or stop. The
recipient answers with `reply`, which lands in your inbox. If your harness is idle the inbox
hands it the reply at once as a new turn; if it is mid-turn the reply waits for the turn
boundary. Nothing polls and nothing blocks. Use `ask` (below) only as a convenience for a short
question you want answered inside the current turn.

### Direct routing: the mesh first, Forge for the rest

Agents in the same flock talk directly, without Forge in the middle.

- **Which path.** For `send_message`, `ask` and a `reply` to a session, the sending Skuld checks
  its mesh peer table (the verified flock members discovery maintains). A recipient named by
  `session_id` is a peer when a peer with that id is in the table; one named by `to` is a peer
  when `to` is a peer id, or the name a Skuld peer announces (its session name). A peer is
  reached Skuld to Skuld by the `peer_message` mesh RPC. Every other recipient goes through
  Forge as before. The result says which: `"path": "mesh"` or `"path": "forge"`.
- **Delivery is acknowledged end to end.** The recipient's Skuld answers the RPC: `accepted`
  (held in its durable inbox; same meaning as Forge's "delivered") or `refused` with the reason,
  which is an error to the sender. Forge records nothing about a mesh message. An RPC that gets
  no answer within `mesh.rpc_timeout_s` is an error for `send_message` ("may or may not have
  arrived: retry with the same request_id"), and `delivery_unknown` for `ask`.
- **No fallback.** With the mesh enabled but not running, a send to any recipient is refused
  with that reason: whether the recipient is a peer cannot be known, and nothing is rerouted
  through Forge. A name two peers carry is an error that lists them; send to the peer id.
- **Stamping is unchanged.** The sending Skuld stamps itself as the sender (session id, name,
  node); the reply route is its own mesh peer id.
- **One peer id.** A Skuld has one mesh peer id: `mesh.peer_id` when configured, else its
  session id (a hostname is not unique across pods, VMs or slots, so it is never used). That
  one id is the transport's RPC and subscribe identity, the id discovery announces, the
  `mesh_peer_id` Forge certifies for message signing, and the target of every mesh reply route.
  A mesh that cannot settle on one id does not start: a session with neither `mesh.peer_id`
  nor a session id, a participant that runs as another id than `mesh.peer_id`, or discovery
  announcing another id than the transport is a startup error naming the ids. Nothing falls
  back to a second id.
- **Caveat.** A name matches only the display name a Skuld announces, the session name; Ravn
  residents are addressed by peer id.

### A send that was not confirmed may have arrived

The message id derives from the sender, the recipient and the `request_id`, so the
`request_id` is the message's identity. When a send is not confirmed, the message may already
be delivered: Forge did not confirm the recipient's durable log in
`forge_mcp.notify_confirm_timeout_s`, the mesh RPC got no answer in `mesh.rpc_timeout_s`, or
Forge could not be reached. The tool then does not say "failed". It says **delivery not
confirmed, the message may have been delivered**, names the `request_id` (the one it generated
if you gave none), and tells you to retry with that same `request_id`. The retry carries the
same message id; the recipient's inbox holds it once and reports the second arrival as a
duplicate. A retry with a different `request_id` is a different message and is delivered
again. A send Forge accepted but could not confirm comes back as `status: pending` with a
`note` that says the same. A plain refusal (unknown session, no grant, inbox off) is a 4xx and
says the message was not delivered.

### Who may message whom

**A session may message any session its owner owns, on any node of its Guild** (decided
2026-11-08). Other owners' sessions are refused with the reason. A session never messages
itself.

- The `message` grant is required. Coordinators and **workers** carry it by default
  (`forge_mcp.role_grants`: `coordinator: [lifecycle, message]`, `worker: [message]`).
  Sessions in no project, and roles you do not list, get none; set `role_grants: {}` to
  grant nothing by role. A worker still has no `lifecycle` grant, so it cannot create,
  start or stop sessions.
- Loops between agents are made visible, not capped. Every message is an envelope in the
  recipient's log with its sender and `in_reply_to`, and there is no hop or rate limit.

### Address by name

`send_message` takes `to` (a session name) instead of `session_id`. The name is looked up
among the sessions the caller can see: its owner's, on every node of the Guild, not
archived.

- An unknown name is an error that names it.
- A name two or more sessions carry is an error that lists them (name, id, node, status).
  Nothing is picked; send again with `session_id`, or add `instance_id` to name the node.
- `get_session` and `list_sessions` show each session's `name`.

## Session MCP tools

The first rows are the default for the two use cases; the direct tools after them are for one specific peer.

| Tool | Needs | What it does |
|---|---|---|
| `mesh_publish(topic, content, correlation_id?, in_reply_to?)` | mesh enabled on this session | **announce an event** on a topic your persona declares it produces, for whoever consumes it; answer a message the same way (see "Events: publish and subscribe"). Listed only when `mesh.enabled` is true |
| `mesh_ask(content, producers_of \| peers, wait_s?)` | mesh enabled on this session | **ask a question** of the peers that produce a topic, or of chosen peers (see "Asking a question"). Listed only when `mesh.enabled` is true |
| `mesh_subscribe(topic \| correlation_id)` / `mesh_unsubscribe(...)` | mesh enabled on this session | follow or stop following a topic or a thread (see "Following topics and threads") |
| `send_message` | `message` grant | send to a session by `to` or `session_id`; optional `urgent` |
| `ask(to / session_id, content, deadline_s?)` | `message` grant | send to a session and **wait for its answer** (see "Asking and waiting") |
| `reply(message_id, content)` | the route's own need | answer a message you received, by the route its sender gave: session, mesh peer or room. A message sent as a notice (no reply route) is refused with the reason |
| `room_post(room_id, host_session_id, content)` | `message` grant | speak in a room another session hosts. The host records the message in the room as said by this session, and refuses it when the session is not seated in that room |
| `mesh_send(peer, content, expect_reply)` | mesh enabled on this session | send a peer message to a mesh peer id. Listed only when `mesh.enabled` is true |
| `mesh_peers(role?, capability?, consumes?, produces?)` | mesh enabled on this session | list the peers on the mesh with what each announced: name, persona, role, description, capabilities, topics consumed and produced, activity state. Facts only (see "Finding a peer"). Listed only when `mesh.enabled` is true |

`room_post` takes the hosting session explicitly: the "Relayed by" line of a message from
the room names the host, and the "Said in room" line names the room. Forge proxies the
call to the host as `POST /sessions/{host}/room/session-message` and stamps the calling
session itself; the room host trusts only that stamp. `reply` to a room message uses the
same call.

## Finding a peer

When an agent needs a specific peer it chooses; the platform does not route for it. Each Skuld announces
itself on the mesh with facts taken from its persona and its mesh config, never typed by the
agent, and `mesh_peers` returns the table of what the session's discovery has heard.

| Field | Where it comes from |
|---|---|
| `peer_id`, `name`, `kind` | the peer's mesh id; the session name (what `send_message` and `ask` take as `to`); `skuld`, `ravn`, ... |
| `persona` | the pinned persona's id, else `mesh.persona` |
| `role` | `mesh.role`, else the pinned persona's id |
| `description` | the pinned persona's `description` (a new optional persona field, a sentence or two on what it does), else `mesh.description` |
| `capabilities` | `mesh.capabilities` |
| `consumes`, `produces` | the pinned persona's `consumes` / `produces` on top of `mesh.consumes_event_types` / `mesh.publish_event_types` |
| `activity_state`, `status`, `last_heartbeat` | the peer's own activity state (`idle`, `active`, `tool_executing`, `awaiting_input`, ...), whether it is working (`busy`), and when it last announced |

The filters (`role`, `capability`, `consumes`, `produces`) are exact matches on one announced
fact and all must hold. The result is in peer id order, which is not a preference: nothing is
ranked, scored or routed, and the choice is the agent's judgment. A field a peer did not
announce is `null` (see below). A session with no
discovery configured gets an error that names the remedy, never an empty table, which would
read as "nobody is there".

**Activity is as fresh as the last heartbeat.** The Skuld updates its announced state when its
activity changes and the next heartbeat carries it, so `activity_state` can lag by a heartbeat
interval. Check `last_heartbeat` before relying on it. A peer that is `awaiting_input` is
blocked on a person.

**What a discovery could not tell you is `null`, not empty.** A fact a peer's announcement
did not carry comes back as `null` and is named in that peer's `not_announced` list. That is
different from a peer announcing an empty role or no topics (`""` and `[]`): it means unknown.
A filter never matches a fact that was not announced. The facts are `role`, `description`,
`activity_state`, `busy` (shown as `status`), `consumes_event_types` and `emits_event_types`
(shown as `consumes` and `produces`).

| Discovery | What it carries | Not announced by its nature |
|---|---|---|
| `event_bus` (over NATS or nng) | everything, refreshed on every heartbeat | nothing; an older peer's missing keys are named |
| `sleipnir` (AMQP) | everything, refreshed on every heartbeat | nothing; an older peer's missing keys are named |
| `mdns` | everything in the handshake, as a snapshot: role, description, display name, topics, activity. After it, the activity state, busy flag and topics follow the peer's re-registered TXT records | description and role are never in TXT (a record is limited to 255 bytes), so they stay as of the handshake |
| `k8s` | role, description and topics, from the pod annotations `ravn.niuu.world/role`, `/description`, `/consumes`, `/emits`, read when the peer is first seen | `activity_state` and `busy` (a pod's annotations are written by its deployer, not by the running peer); and any fact whose annotation is absent |
| `static` | role, description and topics, from the cluster file entry | `activity_state` and `busy` (a file cannot say what a peer is doing); and any fact the entry leaves out |

Announcements from older and newer participants both parse: unknown keys are ignored and
missing ones are `not_announced`.

**When to look a peer up.** Not before every message. To ask whoever produces a kind of
knowledge or work, `mesh_ask(producers_of=<topic>)` finds them for you; to announce something,
`mesh_publish`. Use `mesh_peers` when you want to choose peers yourself, or need a specific
one:

1. `mesh_peers(role="reviewer")` (or by `capability`, or by a topic it `produces` or `consumes`).
2. Read `description` and `activity_state` and pick.
3. Then either ask the ones you picked, `mesh_ask(peers=[...])` (the question goes to
   them and answers come back correlated), or reach one directly: `ask(to=<name>)` to wait for
   its answer, `send_message(to=<name>)` to hand it work without waiting, or
   `mesh_send(peer=<peer_id>)` for a mesh peer that is not a Forge session.

## When discovery fails

Discovery is how a session is seen, so none of its failures is quiet: a failed announce means
peers stop seeing the session, and a failed list means a peer table that is wrong without
saying so. Every adapter (`event_bus`, `sleipnir`, `mdns`, `k8s`, `static`, and the composite
that runs several) follows the same rules (`niuu.adapters.discovery._failures`):

| Failure | What happens | Why |
|---|---|---|
| The adapter is configured but cannot run: a missing package (`aio_pika`, `zeroconf`, `pynng`, `kubernetes`), an AMQP URL that is not set or does not connect, no cluster credentials, no LAN address, a named `cluster_file` that is absent or unreadable, the first announce that cannot be published | `start()` raises `DiscoveryError` with the remedy, and the session's mesh does not start | the operator asked for discovery; running without it is the fallback this avoids. A composite raises for any backend that fails and stops the ones that started |
| A background iteration fails: a heartbeat that cannot be published, a Kubernetes list that fails, a static file that cannot be reloaded or has vanished, an mDNS responder error | error log, counted on `ravn.degraded` as `niuu.discovery.<adapter>.<operation>`, and the loop tries again at the next interval | it cannot raise into nothing, and stopping the loop would end discovery for good. Until a heartbeat succeeds peers age this session out, which is why it is an error and not a debug line |
| A list or reload fails | the peer table is left as it was | an empty result would read as "every peer left" |
| A consumer's `on_join` / `on_leave` callback raises | reported and counted (`<adapter>.join` / `<adapter>.leave`); the peer table is already updated and the other callbacks still run | one consumer's bug must not stop the others or corrupt the table |
| A message from another peer is bad: not JSON, no identity object or `peer_id`, a SPIFFE token that does not verify, an mDNS handshake step that fails its HMAC or is malformed, a candidate with no handshake port | refused, logged at error and counted on `niuu.discovery.refusals` by `adapter` and `reason` (`malformed`, `spiffe_invalid`, `hmac_mismatch`, `no_handshake_port`, `rejected`); it does not raise | a refusal is deterministic, so a retry fails the same way; and one peer's bad message must not stop the consumer for every other peer. Signed announcements are refused and counted by the mesh attestation (`niuu.mesh.attestation.refusals`) |
| One mDNS handshake attempt fails (a timeout, PAIR socket contention) | a counted retry (`ravn.retries`); after three attempts an error | transient, and the retry is bounded and named |

Two things stay quiet because they are the expected steady state, not failures: an
announcement from another realm (peers of other flocks share the network), and a session's own
announcement coming back to it. A `cluster_file` left at its default location may be absent: a
host with no cluster file is normal; one the operator named must exist.

## Configuring the mesh

Everything under Skuld's `mesh` settings (`skuld.config.MeshConfig`) is a value of the skuld
chart's `mesh` block (`charts/skuld/values.yaml`, documented in its README) and renders
into the broker's config file. Forge reaches a session pod through the same Helm values
as for every other setting: the pod manager's `session_defaults`
(`podManager.kwargs.session_defaults` in the volundr chart) is merged under what the session
contributors add, and the result is the session's release values. There is no mesh
contributor: the mesh settings that differ per session (topics, role, description) come from
the session's pinned persona, which Skuld reads itself, so one set of `session_defaults`
serves every session of a flock. Set at least `mesh.enabled`, a `mesh.realmId` shared by the
flock, a transport and a discovery adapter:

```yaml
podManager:
  kwargs:
    session_defaults:
      mesh:
        enabled: true
        realmId: flock-a
        transport: nats
        nats: {servers: ["nats://nats:4222"]}
        discoveryAdapters: [{adapter: event_bus}]
```

Before the chart rendered these values, `mesh.enabled` and `mesh.transport` did nothing in a
chart-deployed session.

## Who sent a mesh message

The mesh transports prove a connection, not a sender. So every message on the mesh carries
its own proof, and a receiver checks it before anything else sees the message. This works
the same on every transport (Sleipnir over nng, NATS or RabbitMQ, the webhook adapter,
in-process): **the signature is what proves the sender, not the transport.** NATS
credentials or subject permissions, where a deployment adds them, are an extra layer
later, never a substitute, and NATS may run across hosts without changing any of this.

**Nothing to set up.** A session with `mesh.enabled` asks the Forge that launched it for a
mesh certificate with the session credential it already holds, using a key it generates in
its own memory. It signs what it sends and verifies what it receives. The keys needed to
verify others arrive in the same answer, so there is no per-message call to Forge.

- **What is proved.** The certificate (a short-lived `mesh_peer` token from Forge) binds the
  peer id, the session and launch, the memberships (tenant, flock, realm) and the peer's
  public key. The sender's session id, peer id, reply route and event `source` must be the
  certified ones; a message that names another sender is refused. It covers `peer_message`,
  `work_request`, `peer_state`, `present_file` and the other mesh RPCs, their replies
  (`accepted`, `refused`, `peer_state` answers), and published events (`mesh_publish`, the
  consumed outcome events). A reply signed by anyone but the peer that was asked is refused.
- **Who may talk.** A sender must share at least one tenant, flock or realm with the
  receiver. Owners are not compared, so a flock of residents and sessions with different
  owners works. Forge sets the memberships from its records: today `tenant:<tenant>`.
- **Revocation.** When a session stops or restarts, Forge announces it on the mesh, signed,
  and receivers drop that launch at once. If a receiver was offline for the announcement, the
  certificate lifetime ends it: `forge_mcp.mesh_identity.ttl_seconds` on Forge (default 900
  s). Sessions renew in the background at half of it (`mesh.attestation.renew_fraction`), so
  a session that runs for hours keeps renewing. The lifetime only bounds how long a leaked
  or stale certificate is believed.
- **Fails closed.** A request that cannot be verified is answered `refused` with the
  `reason`, an event that cannot be verified is not handled, and a reply that cannot be
  verified raises to the sender. Every refusal logs at error and counts on
  `niuu.mesh.attestation.refusals` (`direction`, `reason`, claimed `sender`) and on
  `skuld.peer.messages{outcome="refused"}`; the session status `peer_messages.unverified`
  carries the counts.
- **A session that cannot get a certificate does not start its mesh.** The error names the
  fix: give the Forge that launches it a signing key so that `forge_mcp.session_tokens`
  is on (the Volundr chart then also sets up the Kubernetes and Flux delivery), set
  `volundr_api_url`, or set `mesh.attestation.required: false` to run the mesh
  unauthenticated, which is the operator's explicit choice.

| `reason` | Meaning |
|---|---|
| `missing` | The sender signed nothing (an old build, or `mesh.attestation.required: false` there). |
| `malformed` | The proof is not in the expected shape. |
| `cert_invalid` / `cert_expired` | The certificate is not Forge's, is not a mesh certificate, or its lifetime ended. |
| `unknown_key` | Signed by a Forge key this peer has not got yet. Picked up at its next renewal. |
| `signature_invalid` | The message was changed, or signed for another recipient, purpose or topic. |
| `stale` | Outside `mesh.attestation.clock_skew_s` (requests) or `mesh.attestation.event_max_age_s` (events). Check clocks. |
| `binding_mismatch` | The message names a sender, source or reply route other than the certified one. |
| `not_member` | The sender shares no tenant, flock or realm with this peer. |
| `revoked` | The sender's session stopped or restarted. |
| `not_authority` | Something other than Forge tried to revoke a session. |
| `trust_unavailable` | This peer holds no usable Forge keys. |

**Turning signing off.** It is one decision, `mesh.attestation.required`, and it is **on by
default in every shape**, mini mode included (Forge's own key file signs the certificates, so
it needs no setup). Turning it off is the operator's choice; it is not an error, but it is
logged once at startup by every component and reported:

| Where | How to turn it off | Where it shows |
|---|---|---|
| Mini mode (`niuu platform up`) | `mesh: {attestation: {required: false}}` in `~/.niuu/config.yaml` (`NIUU_MESH__ATTESTATION__REQUIRED=false` also works). The same section carries the local sessions' mesh settings: `enabled`, `transport`, `peer_id`, `realm_id`. It reaches Forge's own signers and every locally launched session's Skuld settings from this one value | Forge logs once at startup; `GET /health` shows `mesh_attestation: {required: false, state: "unauthenticated"}`; each session logs once and reports `mesh_attestation` in its activity metadata |
| Forge in a cluster | Volundr chart `mesh.attestation.required: false` (Forge setting `mesh.attestation.required`). Forge carries it into the sessions it launches through every pod manager (the `mesh` setting; `session_defaults.mesh` for Flux); a different value set there fails startup | same |
| A Skuld by itself | Skuld chart `mesh.attestation.required: false` (setting `mesh.attestation.required`) | the session log and activity metadata |

What it means when off: nothing on the mesh proves its sender. Any peer on the bus can claim to
be any session, send in its name, forge its replies and announcements, and publish events as
it; Forge issues no certificates, signs no resident traffic and publishes no revocation
notices. Peers with signing on refuse everything an unsigned peer sends, so **turn it off
for the whole deployment, not for one component**. Every pod manager that starts Skuld carries the decision into its sessions: the local,
Docker, direct Kubernetes, OpenShell and VM managers take Skuld's `mesh` settings (the
`mesh` kwarg) and hand them over as `SKULD__MESH__...` variables the way they hand over their
other session settings (process and container environment, the sandbox environment, the
pod spec, the VM's launch environment), and the Flux manager through `session_defaults.mesh`.
Forge writes `mesh.attestation.required` into all of them, and a different value set there
fails startup. A pod manager of your own that has no such setting is refused at startup while
signing is off, rather than left to disagree.

**How long a stopped session is still believed.** Plan for the longest of these, not the
shortest:

- *Requests and replies* (`peer_message`, `peer_state`, ...): a certificate is checked when the
  message arrives and the message must be signed within `mesh.attestation.clock_skew_s`, so a
  stolen key works for at most the certificate lifetime plus that skew.
- *Published events:* an event's certificate is judged at the time **the sender signed it**, so
  that an event redelivered by the bus still verifies. A stolen key can therefore sign an event
  with a back-dated time and have it accepted up to `mesh.attestation.event_max_age_s` (default
  1 h) **after the certificate expired**. The bound for an event is the certificate lifetime
  plus `event_max_age_s`.
- *The signed "session stopped" notice* ends all of this at once for every receiver that gets
  it. It travels on the mesh bus. The default bus (`InProcessBus`, `sleipnir.adapter` in the
  charts) delivers only inside one process, so on it a stopped session's notice reaches no
  other pod, and the bounds above are the whole story. A networked bus (NATS, RabbitMQ) delivers
  it to every pod, as long as the receiver is connected when it is sent; a receiver that was
  offline then relies on the bounds above. Lower `forge_mcp.mesh_identity.ttl_seconds` and
  `mesh.attestation.event_max_age_s` to tighten them; the second must stay above the bus's
  redelivery window.

**Discovery announcements are signed too.** Peers choose whom to ask from the peer table
(role, description, topics, activity), so an announcement carries the same proof: a forged one
does not add or change a peer, and a forged `leave` does not remove one. The peer an
announcement describes must be the peer that signed it, and an announcement older than one
already accepted from that peer is refused, so a captured `leave` cannot be replayed after a
newer heartbeat. This covers the discovery adapters that share the mesh bus (`event_bus`,
`sleipnir`). `static` (configuration) and `k8s` (the cluster's own API) do not ride the bus;
`mdns` proves membership with the realm key's handshake.

**Platform events are not mesh messages.** The resident's `ObservationRelay` forwards
events that Forge, Ting and Mímir publish on the bus (a plan was approved, a gate opened). Those
publishers are services, not mesh peers: they hold no mesh certificate, and Forge issues
certificates only to sessions and residents. These events are therefore not signed, and
anyone allowed to publish on the bus can forge one. The relay ignores everything under the mesh
prefix (`ravn.mesh.`), so a mesh peer cannot pass itself off as a platform service through the
mesh. The control that closes the rest is on the bus itself: let sessions publish only under
`ravn.mesh.*` (NATS subject permissions, a RabbitMQ user per session), which is an extra layer
this platform does not set up for you.

**Where the session credential reaches the session** (it is what the certificate request
uses; a session without one cannot start its mesh unless attestation is off):

| Launch path | Credential |
|---|---|
| Local process, OpenShell sandbox | delivered (environment variable, file) |
| VM slots and registered hosts | delivered when `compute.credential_delivery` is configured |
| Kubernetes and Flux session pods | delivered through a per-session Secret. The Volundr chart turns this on by itself wherever Forge has a signing key (workload identity with `signingKey.existingSecret`, or `forge_mcp.session_tokens.signing_key_file`); without a key, session credentials stay off and a session's mesh will not start |
| Docker container pod manager | delivered as a read-only 0600 file mount (needs `run_as_host_user`; a launch that cannot read it is refused with the remedy) |
| Direct Kubernetes pod manager | delivered through the per-session Secret; `volundr_api_url` must be set (the launch is refused with the remedy otherwise) |

## Asking and waiting (`ask`)

`ask` is for one specific session. To put a question to several peers, see "Asking a question".

`ask` is `send_message` that blocks until the recipient answers with `reply`. Addressing
(`to` or `session_id`), the sender stamp, the `message` grant and who may be reached are
exactly those of `send_message`. It serves **session recipients**; a mesh peer or a room
is not asked this way.

**The answer is the result of the call.** It arrives in the asker's own inbox as a peer
message whose `in_reply_to` is the question's `message_id`, and wakes the wait at once (no
polling for the answer). While the call waits, that reply is held back from the turn
boundary, and the call takes it and records it as consumed in the session's durable log. So
it is never also handed to the harness as a separate turn, and a restart does not hand it
over either. A log that cannot store the take fails the call with the reason and leaves the
message in the inbox for normal delivery.

**Every ending is its own named result.** Anything but `answered` is returned with
`isError`, a `detail` saying what to do, and the `request_id`; none of them is an empty
answer.

| `outcome` | Meaning |
|---|---|
| `answered` | The recipient replied: `answer`, `from` (the stamped sender), `answered_at`. |
| `blocked` | The recipient's harness waits on an approval or a question to a person (`activity_state: awaiting_input`). Reported when Forge's next state check sees it (`ask.poll_interval_s`). |
| `gone` | The recipient is `stopped`, `failed` or `archived`, its transport died, or it was deleted. |
| `not_started` | The message is in the recipient's inbox but it took no turn that read it before the deadline. Needs the recipient's Skuld to have reported the message as waiting (it rides its activity report, refreshed at each heartbeat), so a very fresh message may read as `deadline` instead. |
| `delivery_unknown` | Forge's send answer was pending or unconfirmed and no answer came. Whether the recipient has the message is unknown: not failed, not done. |
| `deadline` | The recipient had not answered when `deadline_s` passed. The error names `ask.wait_max_s`. |

**Nothing is lost when the wait ends without an answer.** The message stays sent. An answer
that arrives afterwards is delivered at the next turn boundary like any peer message. To keep
waiting, call `ask` again with the **same `request_id`** (returned in every result): the
message id is the same, so it is not sent twice. Asking again after the answer was already
taken returns that same answer; asking again after a late answer reached the harness returns
it with a note that it is in the context twice.

**The deadline.** `deadline_s` defaults to `ask.wait_max_s` and may not exceed it; a larger
one is refused with the knob named, before anything is sent, and counted as a cap hit. The
real bound is how long the engine lets one MCP tool call run, because the wait happens inside
that call (Skuld's stdio server also waits on the broker for `forge_mcp.broker_request_timeout_s`):

- Codex `tool_timeout_sec` defaults to 60 s (OpenAI Codex MCP configuration); Skuld sets it
  explicitly from `forge_mcp.tool_timeout_s` (default 120 s).
- Claude Code's wall-clock `MCP_TOOL_TIMEOUT` defaults to about 28 hours and does not extend on
  progress; its idle timeout for a stdio server is 30 minutes without a response or progress
  (Claude Code MCP documentation). A server entry's own `timeout` (milliseconds) replaces
  `MCP_TOOL_TIMEOUT` for that server only. Skuld sets it from the same `tool_timeout_sec` it
  gives Codex, so both engines are bounded by `forge_mcp.tool_timeout_s` for the Forge server
  (and for any launch-spec server that declares `tool_timeout_sec`). Skuld deliberately does
  not set the `MCP_TOOL_TIMEOUT` environment variable, which would also shorten the calls of
  every other MCP server.

So `ask.wait_max_s` (default 50 s) must stay below both `forge_mcp.broker_request_timeout_s`
(default 60 s) and `forge_mcp.tool_timeout_s` (default 120 s); a configuration that does not
fit fails at startup. A slow recipient is waited for by calling `ask` again, not by a longer
call.

**What Forge is asked.** Only what Forge alone knows, over routes a session credential already
has: `GET /sessions/{id}` (`forge:session:read`) for the recipient's lifecycle status,
`activity_state` and the messages its Skuld reports as waiting. The send is the existing
`POST /sessions/{id}/messages` (`forge:session:message`). No new Forge route was needed.

**Observability.** Every call increments `skuld.ask.outcomes` once (`outcome`, `recipient`).
A wait that runs into the ceiling itself (`deadline`, `not_started` or `delivery_unknown` with
`deadline_s` at `ask.wait_max_s`) and a refused over-long `deadline_s` also count as a hit of
`ask.wait_max_s` (`ravn.cap.hits`). A shorter deadline the caller chose is its own decision and
counts only on `skuld.ask.outcomes`.

**Asking a mesh peer.** `ask` to a recipient in the mesh peer table takes the direct path and
no Forge call at all. The answer is matched by `in_reply_to` in your own inbox exactly as
above. The send is acknowledged by the recipient's Skuld (`accepted`), or it is an error
(`refused`) with the reason. What Forge would have told you comes from the recipient itself:

| Outcome | Over the mesh |
|---|---|
| `answered` | as above |
| `gone` | the peer is no longer in the peer table, or its Skuld reports its transport stopped |
| `blocked` | its Skuld reports `awaiting_input` (answering the `peer_state` RPC, with the kind of input) |
| `not_started` | its Skuld lists your message among those it accepted and has not handed to the harness |
| `delivery_unknown` | the send RPC got no answer within `mesh.rpc_timeout_s` (it may have arrived) |
| `deadline` | none of the above; names `ask.wait_max_s`. If the peer's state could not be read (its Skuld did not answer `peer_state`, or is not a Skuld) the detail says so |

The `peer_state` RPC is a read of the session's activity state and the ids of its unread
messages; peers are trusted as flock members (realm-verified discovery), and the RPC carries
no message content. A peer that is not a Skuld cannot answer it; `ask` then still returns the
answer or a deadline, never `blocked` or `not_started`.

## Room hosts relay envelopes

A room host delivers to a member session that joined from another session
(`POST /api/room/join` with `session_id`) through Forge. It now sends an envelope:

- the sender is the participant who spoke (a person, a mesh peer or a member session); a
  message nobody in the room spoke, such as the host telling its members what happened, is
  the host's own;
- `relayed_by` is the hosting session, stamped by Forge from the credential the host calls
  with;
- the room, a room reply route and a fresh `request_id` so a retry is not delivered twice.

**Requirements.** The host must hold a Forge session credential with the `message` grant
(`forge_mcp.session_tokens` enabled on its Forge, and `forge_mcp.role_grants` or
`default_grants` giving it `message`). Without one the delivery is refused with 403 and the
host says so. A delivery that fails is an error to the caller of the room route (503), is
counted on `skuld.collaboration.deliveries{outcome="failed"}` and logged at error; it is
never logged and dropped. A member that sits in several rooms needs the message to name
the room (`metadata.room_id`); a member in no room cannot be messaged.

## Not yet done

- The `ravn.mesh` wire prefix keeps its name. New peer-message traffic uses the neutral
  `peer_message` RPC type; renaming the prefix is a wider change.
