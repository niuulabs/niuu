# Agent messaging: sessions, rooms and the mesh

Agents start conversations with each other directly. Ting never relays them. The platform
stamps who sent every message, so an agent cannot pass as the user, as another agent or as
a room participant.

## The envelope

Every message from one agent to another travels as a **peer message**
(`niuu.domain.peer_message.PeerMessage`): message id, sender, recipient, send time,
content, a reply route, an optional `in_reply_to`, a `room_id` when it was said in a room,
`relayed_by` when a room host passed it on, and `urgent`.

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

The request body can add `in_reply_to` and `urgent`. A session that sends `sender`,
`recipient`, `relayed_by`, `sent_at` or `message_id` is refused with 400: Forge stamps all
of them. The message id derives from the sender, the recipient and the `request_id`, so a
retry with the same `request_id` carries the same id and is never delivered twice. A person
calling the same route (a PAT or login) keeps the plain user-turn path.

## Who may message whom

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

## Address by name

`send_message` takes `to` (a session name) instead of `session_id`. The name is looked up
among the sessions the caller can see: its owner's, on every node of the Guild, not
archived.

- An unknown name is an error that names it.
- A name two or more sessions carry is an error that lists them (name, id, node, status).
  Nothing is picked; send again with `session_id`, or add `instance_id` to name the node.
- `get_session` and `list_sessions` show each session's `name`.

## Session MCP tools

| Tool | Needs | What it does |
|---|---|---|
| `send_message` | `message` grant | send to a session by `to` or `session_id`; optional `urgent` |
| `ask(to / session_id, content, deadline_s?)` | `message` grant | send to a session and **wait for its answer** (see "Asking and waiting") |
| `reply(message_id, content)` | the route's own need | answer a message you received, by the route its sender gave: session, mesh peer or room. A message sent as a notice (no reply route) is refused with the reason |
| `room_post(room_id, host_session_id, content)` | `message` grant | speak in a room another session hosts. The host records the message in the room as said by this session, and refuses it when the session is not seated in that room |
| `mesh_send(peer, content, expect_reply)` | mesh enabled on this session | send a peer message to a mesh peer id. Listed only when `mesh.enabled` is true |

`room_post` takes the hosting session explicitly: the "Relayed by" line of a message from
the room names the host, and the "Said in room" line names the room. Forge proxies the
call to the host as `POST /sessions/{host}/room/session-message` and stamps the calling
session itself; the room host trusts only that stamp. `reply` to a room message uses the
same call.

## Asking and waiting

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
  (Claude Code MCP documentation). Skuld's own stdio server is the tighter one.

So `ask.wait_max_s` (default 50 s) must stay below both `forge_mcp.broker_request_timeout_s`
(default 60 s) and `forge_mcp.tool_timeout_s` (default 120 s); a configuration that does not
fit fails at startup. A slow recipient is waited for by calling `ask` again, not by a longer
call.

**What Forge is asked.** Only what Forge alone knows, over routes a session credential already
has: `GET /sessions/{id}` (`forge:session:read`) for the recipient's lifecycle status,
`activity_state` and the messages its Skuld reports as waiting. The send is the existing
`POST /sessions/{id}/messages` (`forge:session:message`). No new Forge route was needed.

**Observability.** Every call increments `skuld.ask.outcomes` once (`outcome`, `recipient`).
A wait that reaches its deadline (`deadline`, `not_started`, `delivery_unknown`) and a refused
over-long `deadline_s` also count as a hit of `ask.wait_max_s` (`ravn.cap.hits`).

**Mesh peers.** A wait on a mesh peer would need the same three things on the mesh path:
`mesh_send` to return the derived `message_id`, the answer to arrive as a peer message whose
`in_reply_to` matches (the sender's Skuld inbox already holds it), and a state source for
"peer blocked or gone" from the flock membership instead of Forge's session row. It is not
built.

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
