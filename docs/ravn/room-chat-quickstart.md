# Chatting with a Ravn — quickstart

How to give a Ravn a persistent chat surface you can build a client against:
history replayed on connect, turns durable across restarts, live streaming.
No Volundr, no Postgres, no Kubernetes.

A Ravn already runs standalone (`ravn run`, `ravn gateway`, `ravn daemon`).
What it has no durable version of is a *conversation* — its own HTTP gateway
keeps sessions in memory, mints a new session id per WebSocket connection, and
serves no history. A **room** puts a Skuld broker in front of the Ravn to hold
the transcript. Ravn keeps judgment and memory; the broker owns the transcript
and the WebSocket.

## Prerequisites

- `dev` at `7b0462b3` or later. Earlier revisions of `ravn room create` write a
  config that routes your chat to a stray Claude Code process instead of the
  Ravn — silently, with no error.
- A model backend the Ravn can reach (whatever you already use).
- For the tmux persona below: `tmux` and an authenticated `claude` on `PATH`.

## 1. Create the room

```bash
ravn room create desk
```

Writes `~/.ravn/rooms/desk/`, starts the broker, and waits until it answers.
Prints the broker URL. `ravn room ls|show|start|stop|rm` manage it.

## 2. Write a persona

A persona says who the Ravn is and which backend runs it. It is required —
`ravn join` refuses to start without one. `ravn personas list` shows the
built-ins, but they are nearly all specialists; for a general steward start
here (`damien-steward.yaml`):

```yaml
name: damien-steward
system_prompt_template: |
  You are a resident Ravn operating inside a durable daemon.
  Orient from the mandate, inspect memory before acting, create and advance useful work,
  ask before risky external effects, and persist clear evidence for every decision.
allowed_tools:
  - file
  - git
  - terminal
  - web
  - todo
  - introspection
  - mimir
  - workflow
  - platform
  - ask_user
permission_mode: full-access
iteration_budget: 80
executor:
  adapter: ravn.adapters.executors.cli.CliTransportExecutor
  kwargs:
    transport_adapter: skuld.transports.tmux_interactive.TmuxInteractiveTransport
```

The `executor` block is what runs the Ravn on tmux Claude instead of a direct
LLM loop. Ravn's own tools stay available — they are injected into the Claude
session as an MCP server, so Claude drives execution while Ravn keeps
judgment, memory, and continuation. Swap `transport_adapter` for
`skuld.transports.codex_ws.CodexWebSocketTransport` to run on Codex (what
platform-deployed residents use), or drop the `executor` block entirely to use
Ravn's own LLM loop.

The system prompt above describes an autonomous resident that goes and does
work. For a Ravn that mostly sits in the room and answers, rewrite it — that
one field changes the character of the agent more than anything else here.

## 3. Put the Ravn in the room

```bash
ravn join --persona ./damien-steward.yaml --room desk --as steward
```

Blocks until the Ravn actually registers, so a reported join is a join.
`ravn room members` shows who is in and whether their process is alive;
`ravn leave --as steward` removes one.

## 4. Connect a client

WebSocket to `ws://<host>:<port>/session`. On connect, in order:

```
system → capabilities → conversation_history → room_state
```

`conversation_history.turns[]` is the full prior transcript (`id`, `role`,
`content`, `participant_id`) — render it and you have scrollback for free.

Send a message either way:

```json
{"type": "directed_message", "targetPeerId": "steward", "content": "hello"}
```

```json
{"content": "hello"}
```

With one Ravn in the room both reach it. `targetPeerId` is camelCase; read the
peer id from `GET /api/room/participants` rather than assuming it — it is the
`--as` handle.

Replies arrive as `room_message` frames carrying `participantId`.
`room_activity` and `room_agent_event` are the working/typing indicators.

Turns persist to `<room>/workspace/.skuld/conversation_<session>.json` and are
replayed on every connect, including after a full broker restart.

## Limits

- **No Postgres log by default.** Durability is the broker's on-disk JSON,
  which is enough for a chat client. Rooms carry a real session UUID
  (`session_id` in `room.yaml`, mirrored into `broker.yaml`), so adding
  `volundr_api_url` for the durable log and the replay-as-live socket just
  works. A room created before session ids existed still has the room name as
  `session.id` — the broker now refuses to start against Volundr with one of
  those (no more silent 422 loop); recreate the room.
- **No auth on the room broker.** Fine on localhost; do not expose it as-is.
- **~4s reconnect** after a broker restart. Messages sent in that window fail
  with `Unknown room participant`; retry client-side.
- **Two or more Ravns** in one room require an explicit `targetPeerId`. The
  broker reports the ambiguity rather than guessing.

## Troubleshooting

**A reply arrives but sounds like a generic coding assistant, not your Ravn.**
The broker started its own CLI agent. Confirm `room.routed: true` is in
`~/.ravn/rooms/<name>/broker.yaml`, and that the log says `Room-routed session
— skipping transport auto-start`. If it instead says `transport not alive,
starting...`, the config predates the fix — recreate the room.

**`error: Unknown room participant`.** The Ravn is not registered right now.
Check `ravn room members`; after a broker restart give it ~4s.

**Nothing comes back at all.** Check the member log at
`~/.ravn/rooms/<name>/logs/member-<handle>.log` — model auth failures surface
there and are also delivered into the room as an error turn.
