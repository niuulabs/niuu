# Voice calls with a session

The owner of a running Forge session can hold a realtime voice call that is bound to
that one session: talk about it, steer it and hear what it did. This works for any
session Forge launches (Claude Code, Codex and the other harnesses), not only for
residents.

The phone runs the realtime model itself (OpenAI Realtime or xAI Grok Voice) and talks
to the provider directly. The session never relays audio. It gives the phone what the
call needs:

- the voice persona (its voices and how it sounds);
- the call's instructions, grounded in this session: what it is, its state, what it
  asks and its latest turns;
- a short-lived provider credential, which Forge mints from the owner's own API key;
- three tools that act on this session;
- the call record, kept in the session's own event log.

The owner's provider API key never enters the session pod. The coding agent there can
run arbitrary code, so only Forge holds the key and only short-lived secrets leave it.

## How it fits together

```
 phone ──WS /voice, REST /voice/api──▶ session's Skuld broker (skuld.voice)
   │                                     │  persona, instructions, tools, call record
   │                                     └─POST /api/v1/forge/sessions/{id}/voice/client-secrets
   │                                          (its forge_session credential, scope forge:session:voice)
   │                                                 │
   │                                       Forge reads the owner's key from its credential
   │                                       store and mints at the provider's
   │                                       /v1/realtime/client_secrets
   └──── audio, with the short-lived secret ───────▶ provider (OpenAI / xAI)
```

- **The channel** is part of Skuld, the session's gateway (`src/skuld/voice/`). It is
  served on the broker's own authenticated surface, beside the chat WebSocket: the
  WebSocket at `/voice` and REST under `/voice/api`. It speaks the Lexi app's
  agent-service wire (`lexi-agent-service-v1`). Only the session's owner is served:
  every request must carry the owner in the verified `x-auth-user-id` header. Anyone
  else gets 403, or the WebSocket close code 4403.
- **Minting** is Forge's (`volundr.domain.services.session_voice`). The route admits only
  the session's own `forge_session` credential, for its own session. It also requires
  that the session's current launch runs the voice channel. Each provider is a dynamic
  minter adapter. A provider that is not configured, a missing key or a provider refusal
  is an error with the reason. Forge never tries another provider.
- **The endpoint** is advertised on the session. While a session runs with the channel,
  `GET /api/v1/forge/sessions/{id}` returns
  `"voice_endpoint": {"kind": "voice", "protocol": "lexi-agent-service-v1", "url": "…/s/{id}/voice"}`.
  The URL is the session's chat endpoint with `/session` swapped for `/voice`, over HTTP(S).
  It is `null` when the session is stopped, or when its current launch does not run the
  channel (for example, it started before voice was enabled).

## Setting it up

1. **Forge session credentials must be on.** The broker mints through Forge with its own
   session credential. On a cluster, that means `workloadIdentity` with
   `signingKey.existingSecret`, or `forgeMcp.session_tokens.signing_key_file`. The
   Volundr chart refuses to render voice without them, and Forge refuses to start.

2. **A default voice persona.** A session speaks with its own persona's `voice:` section
   when that persona has one. Otherwise it uses the default persona. Add a persona with a
   `voice:` section to the persona catalog:

   ```yaml
   name: lexi
   description: The owner's voice assistant for their Forge sessions.
   system_prompt_template: "…"
   voice:
     voices:
       openai-realtime: marin
       grok-voice: Ara
     style: Warm, brief, plain words. One or two sentences at a time.
   ```

   The call uses only the persona's name, its description and its `voice:` section. The
   rest of a session's persona is the coding agent's instructions, and the voice model
   must not take them as its own. Put the voice's personality in `voice.style`.

3. **Each owner stores their provider key** as a user credential in Forge's credential
   store, under the name and field the provider is configured with (the default field is
   `api_key`):

   ```http
   POST /api/v1/credentials/user
   {"name": "openai", "secret_type": "api_key", "data": {"api_key": "sk-…"}}
   ```

4. **Turn it on** in the Volundr chart:

   ```yaml
   voice:
     enabled: true
     defaultPersona: lexi
     providers:
       openai:
         adapter: volundr.adapters.outbound.realtime_voice.OpenAIRealtimeMinter
         kwargs: {ttl_seconds: 600, timeout_seconds: 12}
         credential_name: openai
         credential_key: api_key
       xai:
         adapter: volundr.adapters.outbound.realtime_voice.XaiRealtimeMinter
         kwargs: {ttl_seconds: 600, timeout_seconds: 12}
         credential_name: xai
         credential_key: api_key
   ```

   In mini mode, set the same `voice:` section in Forge's config file.

From then on, every session Forge launches runs the channel. Forge sets
`SKULD__VOICE_CHANNEL__ENABLED=true` and hands the default persona to Skuld as
`SKULD__VOICE_CHANNEL__DEFAULT_PERSONA`. Sessions that are already running get the channel at
their next start. A launch fails, naming the persona, when `defaultPersona` is not in the
owner's catalog or has no `voice:` section.

The Skuld chart's `voice:` values tune the channel: display name, time zone, the realtime
model per provider, and the waits and limits in the table below. Null keeps the broker
defaults. A misspelt `SKULD__VOICE_CHANNEL__…` setting stops the broker instead of being ignored.

## What the call can do

| Tool | What it does |
|---|---|
| `send_to_this_session` | Sends the person's instruction into the session as their message: recorded as theirs, shown in the transcript as sent from a voice call (a `voice-call-` request id), and delivered the way a typed message is. It answers *delivered* once the agent took it. It answers *queued* when the agent is still busy after `voice.send_confirm_seconds`: the message is accepted and taken next. It answers *failed* with the reason. The voice says which, and never says "sent" for a queued or failed message. |
| `this_session_status` | The session now: working or idle and for how long, what it asks the person, the last message to it and its latest answer. |
| `read_this_session_in_full` | The whole conversation, within one tool result. |

There are no other tools. The call steers this session, and the session does the work.

## The call record

Every `session.start`, `session.log` entry, `session.end` and title or pin change is one
`voice_call` frame in the session's own durable event log. The record lives in Forge with
the session's transcript, not in the pod. The frames are durable only: they are never
broadcast to the session's clients, and the transcript reducer skips them. When the broker
starts, it rebuilds the call list from them while it hydrates the session's history. With
voice on, Skuld requires `event_log_enabled` and `history_hydration_enabled`.

An interim speech entry the phone marks `isLive` is acknowledged but not recorded, because
the next entry replaces it. Deleting a call hides it from the list. Its frames stay in the
log.

If the call list cannot be restored at start, the channel says so instead of showing an
empty history. `/voice/health` reports `degraded`, the history routes answer 503 with the
reason, and `ravn.degraded` counts `skuld.voice.history`. New calls are still recorded.
Restart the session to read the history again.

## Limits

| Knob | Default | When it is reached |
|---|---|---|
| `voice.card_recent_turns` | 6 | The card lists the newest turns and says how many earlier turns it leaves out. The full read reads them. |
| `voice.card_turn_max_chars` | 1500 | A longer turn is cut on the card with a marker, and counted. |
| `voice.full_read_max_chars` | 65536 | The realtime client clamps a tool result at 64 KiB. A full read keeps the newest turns that fit and says how many it left out, and is counted. |
| `voice.send_confirm_seconds` | 15 s | The send answers *queued*. It must stay below `voice.tool_timeout_seconds` (60 s). |
| `voice.mint_timeout_seconds` | 20 s | The phone gets a `PROVIDER_ERROR` with the reason. |
| `voice.providers.<name>.kwargs.ttl_seconds` | 600 s | The provider refuses the expired secret, and the app mints again. |
| `voice.providers.<name>.kwargs.timeout_seconds` | 12 s | The mint fails with 502, logged and counted. |

The rows are also in [limits.md](limits.md) and [forge-limits.md](forge-limits.md).

## Failures and what they mean

| Answer | Cause | Remedy |
|---|---|---|
| WebSocket close 4403, REST 403 | The caller is not the session's owner, or no verified identity reached the broker | Call as the owner, through Forge's session route |
| REST 404 on `/voice/…` | The session does not run the channel | Enable `voice` in Forge and restart the session |
| `PROVIDER_ERROR` naming HTTP 409 | The owner has no credential under `credential_name` with the field `credential_key` | Store the key in Forge's credential store |
| `PROVIDER_ERROR` naming HTTP 422 | Forge has no minter for that provider | Add it to `voice.providers` |
| `PROVIDER_ERROR` naming HTTP 502 | The provider refused or did not answer (the provider's own message is quoted) | Fix the key or the provider account |
| `PROVIDER_ERROR` naming HTTP 409 "does not run the voice channel" | The session's current launch predates voice | Restart the session |
| `UNSUPPORTED_PROVIDER` | The persona has no voice for that provider, or the provider is not `openai` or `xai` | Add the voice under `voice.voices` |

## For the Lexi app

The app already speaks this wire to a resident. To call a session, it reads the session's
`voice_endpoint`. It opens the WebSocket on `url` (to `…/voice`), sends REST to
`url + /api/…` with the owner's own bearer token, and uses the server's
`send_to_this_session`, `this_session_status` and `read_this_session_in_full` instead of
its own device-side session tools. Ringing the phone from a session is not part of this
channel: `call.presence` and `push.register` are answered as not registered.

## Team calls

A call can also be bound to a room: a Forge team's room (`POST /api/v1/forge/teams`), or a
workflow run's room. The room's host is the team's root session. It runs no agent of its
own, so the call there is the person's voice in the room: it speaks to the agents, and the
agents speak back in their own voices. The call is set up the same way as a session call:
the host advertises its `voice_endpoint` while it runs, only the owner is served, and Forge
mints the credential.

The broker picks the binding when it starts. A broker that hosts a routed room
(`room.enabled` and `room.routed`) binds the call to the room. Every other broker binds it
to its own session. Both are implementations of `VoiceCallBinding`
(`src/skuld/voice/binding.py`).

### What the call starts with

The call's instructions open with a **room card**:

- the roster: each agent's name in the room, its persona, whether it leads and whether it
  is seated now, and the people in the room;
- the room's latest posts (`voice.card_recent_turns`; the card says how many it leaves out,
  and a long post is cut and counted, as on a session card).

A team's roster is the team as Forge launched it (`SKULD__VOICE_CHANNEL__MEMBERS`), joined with who
is seated now. A run's room has no such list, so its roster is the agents seated in it.

### What the call can do in a room

| Tool | What it does |
|---|---|
| `say_to_member` | Sends the person's message to one agent by its name in the room. It is a directed room message authored as the owner: the agent records it as the owner's own message, and the room shows it like a typed one. |
| `say_to_everyone` | Sends it to every agent at once, as the team's room delivers an untargeted message (`untargeted_delivery: everyone`). Offered in a team's room only. A run's room takes messages for one agent at a time. A partial delivery is reported as failed, naming the agents it missed. |
| `room_status` | Who is in the room now, and what each agent last said. |
| `read_room` | The room's whole conversation, within one tool result. |

### Hearing the agents

While a phone is on the call (between its `session.start` and `session.end`, or until its
socket closes), every post the room keeps reaches the phone as frames. The phone receives
them in post order, one at a time:

- **Every post becomes a `room.note`:** context for the voice model, never read out.
- **A post that ends with a `Spoken:` line** also becomes a `speak` frame: the line is said
  in the agent's own voice.
- **A speakable post without such a line** (an answer, a question, an error) has
  `speak: true` and a `say` instruction. The host voice then mentions it in one sentence
  in its own voice ("Reviewer posted a long review, want the gist?") and never reads it
  out. Markdown and code are never read aloud.
- **Notices and people's posts** are context only.
- **Agents talking to each other** is context only: a post that opens with `@<agent>`,
  `@everyone` or `@whoever` (also the full-width `＠`), as an agent's `mesh_send`, `reply` or
  `mesh_ask` to a teammate is echoed in the team's room. It is their conversation, not news
  for the person, so it is never spoken, even with a `Spoken:` line. A post that names the
  person, or mentions an agent later in the text, stays speakable.

Whether and when a note is spoken is up to the phone: the same quiet gate and rate rules
as a session call's updates. `speak: true` only marks a note the phone may speak.

**Agents' spoken lines.** While someone is on the call, every message the call sends tells
the agent so, in a note only the agent's model reads (`model_note`; never shown in the room).
The note asks the agent to end its reply to the person with one line:
`Spoken: <what to say aloud>`, at most 600 characters, plain words, no markdown. That is the
same bound as notify's `speak` (`MAX_SPEAK_TEXT_CHARS`). A longer line is not cut: the host
announces the post instead, and the hit is counted (`notify.speak.max_chars`). Posts nobody
asked for carry no spoken line, so the host announces them.

The note and the author reach a member only from the host of a room that member is seated
in (`room.seats`), and only when the mesh verified that host if it requires attestation.
A `model_note` from any other sender is refused. An `author` from any other sender is
dropped with a warning, and the message is delivered without it.

### Voices

Each agent speaks in its persona's voice for the call's provider (`voice.voices`:
`grok-voice`, `openai-realtime`). `POST /teams` can override it per agent:

```json
{"members": [
  {"model": "claude-sonnet-4-6", "persona_name": "reviewer", "lead": true,
   "voices": {"grok-voice": "rex", "openai-realtime": "ash"}},
  {"model": "claude-sonnet-4-6", "persona_name": "coder", "voices": {"grok-voice": "leo"}}
]}
```

Grok has five built-in voices: eve, ara, rex, sal and leo. A custom voice id is accepted as
it is. The launch refuses a team, with 422 and before anything is created, when:

- two agents share a voice on the same key;
- an agent has the host's voice, which comes from the persona named by
  `voice.default_persona`;
- `voices` is given while voice is off.

An agent with no voice for the call's provider is spoken in the host voice, and the phone
names it first. `GET /sessions/{id}/members` shows each agent's resolved `voices`.

### The `speak` and `room.note` frames (for the Lexi app)

Both are sent by the session on its own initiative, in the wire's `{type, id, payload}` shape,
only to a phone that is on a call. They are never replies.

```json
{"type": "room.note", "id": "room.note-…", "payload": {
  "postId": "…", "member": "reviewer", "persona": "reviewer", "kind": "answer",
  "text": "[reviewer posted, answer] …", "speak": true,
  "say": "In one short sentence in your own voice, tell the person that reviewer posted a long reply (812 words), and offer the gist. Do not read it out."}}
```

```json
{"type": "speak", "id": "speak-…", "payload": {
  "postId": "…", "member": "reviewer", "persona": "reviewer", "kind": "answer",
  "text": "The tests pass; one flaky case, I've pinned it.",
  "voices": {"grok-voice": "rex", "openai-realtime": "ash"}}}
```

`kind` is one of `answer`, `question`, `error`, `notice` or `person`. A `speak` frame always
follows the `room.note` of the same post, so the model already holds the context.

What the phone does with them:

1. **`room.note`.** Add `text` to the model's context. When `speak` is true and the quiet
   gate allows, let the host respond following `say`. With `speak: false`, add the context
   and nothing else.
2. **`speak`.** Queue it, first in, first out. When the live response is done and the person
   is not speaking:
   - send `session.update {voice: voices[<provider key>]}`;
   - add `text` as a **user-role** conversation item asking the model to read it exactly,
     word for word, as `member`, then a plain `response.create`. On Grok Voice 3.0 a
     mid-call `response.create` whose line comes as a system item with reply instructions
     is ignored (no `response.created`); the user-role item is said word for word;
   - right after that response's `response.done`, send `session.update` back to the host
     voice, so the person's next turn is answered in the host voice.

   With no voice for the provider, keep the host voice and say "<member> says:" before the
   line. Probed on Grok Voice 3.0: one session switched eve → rex → eve → leo, and each
   line came out in the asked voice and word for word.
3. **A switch while a line is playing.** Never switch voices mid-response. A `speak` that
   arrives while another line, or the host, is speaking waits in the queue. Switch only
   between responses.
4. **Server VAD with a live mic.** If the person starts speaking during an agent's line,
   cancel that response, restore the host voice at once and drop the rest of that line. The
   person's turn is answered by the host. Keep the queued lines, but speak the next one only
   once the person has finished and the host's answer is done.
5. **Long lines.** A spoken line is at most 600 characters, and the server never sends a
   longer one: it sends the host announcement instead. The phone needs no limit of its own.

These three cases (a switch while a line plays, server VAD with a live mic, long lines) were
not part of the probe. The server's ordering and bounds are covered by tests. The phone's
behaviour above is the spec for the Lexi build, which is a separate change in
`~/git/xteo/lexi-ios`.

### A workflow run's room

The same binding serves a Ting run's room, whose root is a routed host with no agent. Its
steps and gates are notices, so they are context only. Two differences:

- **No `say_to_everyone`.** The run's room refuses untargeted messages for its harness
  agents (`untargeted_delivery: refuse`), so the tool is not offered.
- **Host voice only.** The run's agents' voices are not handed to the run's root today
  (`SKULD__VOICE_CHANNEL__MEMBERS` comes from a team launch), so their `speak` frames carry no
  voices, and the phone says their lines in the host voice, naming each agent first.
