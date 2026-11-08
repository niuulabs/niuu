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
| `reply(message_id, content)` | the route's own need | answer a message you received, by the route its sender gave: session, mesh peer or room. A message sent as a notice (no reply route) is refused with the reason |
| `room_post(room_id, host_session_id, content)` | `message` grant | speak in a room another session hosts. The host records the message in the room as said by this session, and refuses it when the session is not seated in that room |
| `mesh_send(peer, content, expect_reply)` | mesh enabled on this session | send a peer message to a mesh peer id. Listed only when `mesh.enabled` is true |

`room_post` takes the hosting session explicitly: the "Relayed by" line of a message from
the room names the host, and the "Said in room" line names the room. Forge proxies the
call to the host as `POST /sessions/{host}/room/session-message` and stamps the calling
session itself; the room host trusts only that stamp. `reply` to a room message uses the
same call.

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
