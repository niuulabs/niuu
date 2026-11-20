# Native-control cards: events, staleness and dismissal (LEX-1936)

A Claude Code session in tmux shows some controls in its terminal: a permission menu
("Yes / Yes, and don't ask again for … / No") or an `AskUserQuestion` menu. Skuld bridges each
one to clients as a **native-control card** (`request_id` `tty-<n>-<hex>`). While a card is
open, Claude cannot take typed input safely, so Skuld refuses messages to the session.

On 5 Nov 2026 a card stayed open for 39 minutes after the CLI had resolved its prompt by
itself, and every steer and coordinator message was refused for that time
(`fleet-ops/reports/incidents/2026-11-05-overnight-perf-stale-native-control.md`). This
page describes how a card now opens, how it closes, and what clients and API senders see.

## Card opened: `ask_user_question`

```json
{
  "type": "ask_user_question",
  "event_type": "ask_user_question",
  "request_id": "tty-1-c3b257b9",
  "tool_use_id": "toolu_01r8JHXnv9",
  "questions": [{"header": "Bash", "question": "Run `rm $V/$n`?", "options": [...]}],
  "metadata": {
    "source": "tmux_tty_bridge",
    "control_kind": "permission",
    "requires_all_answers": true,
    "opened_at": "2026-11-05T20:36:56.590000+00:00",
    "person_only": true,
    "auto_deny_after_seconds": 120.0
  }
}
```

- `opened_at` (new) is when Skuld surfaced the card. Show the card's age from it.
- `person_only` and `auto_deny_after_seconds` (new) appear only on a permission prompt that
  Claude Code shows **despite** `bypassPermissions`: its built-in "only a person may approve"
  safety checks. Claude denies the command by itself after about two minutes, so the app
  should make the card prominent and show a countdown. `auto_deny_after_seconds` is the
  Skuld setting `tmux_person_only_prompt_timeout_s` (default 120). Skuld does not enforce it:
  it states Claude Code's own timeout.

The session enters `activity_state=awaiting_input` at once. For a person-only prompt the
activity metadata is `kind=permission`, `person_only=true`, `urgent=true` and
`auto_deny_after_seconds`, and the prompt says, for example: "Only a person may approve
this: Run `rm $V/$n`? Claude Code denies it by itself after about 120 seconds unless someone
answers." Forge records that attention notification at once, titled "<session> needs your
input now (denied by itself in about 2 minutes)". Its severity stays `warning`: the prompt
fails closed (the CLI denies the command), so it does not override the owner's quiet hours.

### A permission card's options are the live menu's rows (LEX-1950)

Claude Code draws a permission menu only after its `PermissionRequest` hook returns. Skuld
registers the card at the hook (messages are refused from then on) and sends it once the menu
is on screen, at most `SKULD__TMUX_MENU_RENDER_WAIT_SECONDS` (default 1.5 s) later. Its
`options` are the menu's own rows, in order, as Claude Code 2.1.319 draws them:

| Prompt | Options |
|---|---|
| Bash | Yes · Yes, and don’t ask again for: python3 \* · Yes, and switch to auto mode · auto mode handles these prompts for you · No |
| Bash outside the project | Yes · Yes, and always allow access to /home/thor/tmp from this project · Yes, and switch to auto mode · … · No |
| Edit | Yes · Yes, and switch to accept edits (auto-approve file edits and common file commands) for this session (shift+tab) · No |
| WebFetch | Yes · Yes, and don't ask again for example.com · No, and tell Claude what to do differently (esc) |
| MCP tool | Yes · Yes, and don't ask again for gbrain — Get Stats commands in /home/thor/proj · No |
| Person-only check (any mode) | Yes · No |

Answer with an option's label. The generic answers `Allow`, `Allow & don't ask again` and
`Deny` are accepted on every card too (Telegram's buttons, and a card sent with them because
its menu had not rendered in time). Skuld maps them onto the live menu: `Allow` is the
one-time "Yes" row, `Deny` and every "No" row decline with Escape, and `Allow & don't ask
again` is the first row that keeps allowing this kind of request ("don't ask again", with a
straight or typographic apostrophe; "always allow"; "allow all"; "accept edits"; "for this
session"). "Switch to auto mode" is never that row: it lets Claude approve every later prompt
by itself.

A choice the live menu does not have is refused with no keys sent, and the message names the
menu's own options, for example: `The live Claude menu has no "Allow & don't ask again"; no
keys were sent. It offers "Yes", "No": choose one of those`. When the card still showed the
generic options, Skuld sends it again with the live rows (same `request_id`). A client should
replace an open card's options when the same `request_id` arrives again.

## Menu state: `native_control_state`

Skuld checks open permission and question cards on every message delivery and on a periodic sweep
(`tmux_native_control_sweep_interval_s`, default 30 s). Each check looks at the session's
main pane for the menu render window and classifies it:

| `menu_state` | Meaning |
|---|---|
| `live` | A recognizable Claude menu is on screen and the chat composer is not. |
| `gone` | The empty chat composer is on screen and no menu is. |
| `unknown` | Neither or both, or the capture failed. Skuld never clears a card on `unknown`. |

When a card's state changes to `live` or `unknown`, Skuld emits:

```json
{
  "type": "native_control_state",
  "event_type": "native_control.state",
  "request_id": "tty-1-c3b257b9",
  "control_kind": "permission",
  "tool_name": "Bash",
  "tool_use_id": "toolu_01r8JHXnv9",
  "opened_at": "2026-11-05T20:36:56.590000+00:00",
  "age_seconds": 31.2,
  "menu_state": "live",
  "menu_checked_at": "2026-11-05T20:37:27.800000+00:00",
  "absent_checks": 0,
  "superseded_by": null,
  "person_only": true,
  "auto_deny_after_seconds": 120.0,
  "answer_in_flight": false,
  "answerable": true
}
```

A `gone` state is not sent on its own; it is reported by the dismissal below.

## Card closed: `ask_user_resolved`

Every close is the existing `ask_user_resolved` event. A card closed without an answer from a
client now carries the dismissal fields:

```json
{
  "type": "ask_user_resolved",
  "event_type": "ask_user.resolved",
  "request_id": "tty-1-c3b257b9",
  "decision": "completed_elsewhere",
  "accepted": false,
  "dismissed": true,
  "dismiss_reason": "resolved_in_terminal",
  "dismiss_detail": "resolved in terminal",
  "dismiss_trigger": "sweep",
  "control_kind": "permission",
  "tool_use_id": "toolu_01r8JHXnv9",
  "opened_at": "2026-11-05T20:36:56.590000+00:00",
  "age_seconds": 150.4,
  "menu_state": "live"
}
```

| `dismiss_reason` | `dismiss_detail` | When | `decision` |
|---|---|---|---|
| `resolved_in_terminal` | `resolved in terminal` | The card's tool call finished: a `PostToolUse` / `PostToolUseFailure` or `PermissionDenied` hook for it, or its `tool_result` in the native transcript (a timed-out prompt fires no hook). | `completed_elsewhere` |
| `menu_not_shown` | `menu no longer shown` | The menu stayed `gone` for the whole render window, at a delivery or a sweep: once for a permission card, `tmux_native_control_absent_checks` checks in a row for a question card. | `menu_not_shown` |
| `superseded` | `the CLI moved on past it (<hook> for <tool_use_id> at <time>)` | A later `UserPromptSubmit` or `PreToolUse` (for another tool call) from the main conversation arrived after a main-agent card's menu had time to draw, and the next check found the menu `gone`. That check runs at once. | `superseded` |
| `dismissed_by_operator` | `dismissed by operator <principal>` | The dismiss endpoint below. | `dismissed_by_operator` |
| `turn_ended` | `turn ended` | The agent's turn ended with the card still open (unchanged behaviour). | `turn_ended` |
| `closed_for_message` | `closed so a message could be delivered (Chat about this)` or `(Esc)` | A message arrived while a question was live; Skuld closed it the CLI's own way and Claude's result confirmed the close (LEX-2016, below). | `closed_for_message` |

`dismiss_trigger` says which check closed it: `tool_result`, `PermissionDenied`, `delivery`,
`sweep`, `hook` (the check a superseding hook started), `operator` or `turn_ended`. When the transcript proves the result and no hook
reported it, Skuld also emits that `tool_result` as a `user` frame
(`metadata.source=claude_native_transcript`), so the tool card closes too.

Clients drop the card on any `ask_user_resolved` for its `request_id`, as before. A card with
`dismissed: true` can show "Dismissed: <dismiss_detail>" in the timeline.

Permission and question cards are both cleared by these checks (LEX-1979). A question answered
in the terminal closes on its transcript result, as a permission does. A question whose menu is
gone needs `tmux_native_control_absent_checks` `gone` checks in a row, at most one counted per
sweep interval (so a burst of refused messages while a menu is still drawing cannot close it);
any check that is not `gone` starts the count again. It needs them because its tool can be dropped
without any result: on 8 Nov 2026 an auto-compaction left ios-coordinator's question card open
for six hours with no tool run, `menu_checked_at` never set (the sweep skipped questions) and
every message refused. A card whose answer is in flight or uncertain is never cleared
automatically.

A superseding hook is evidence, not proof: Claude takes no prompt and starts no new tool while one
of its own menus waits, but a menu could still be on screen for another reason. So a superseded
card closes only on a `gone` check (one is enough), never on `unknown`, and a check that sees the
menu `live` withdraws the evidence. A `PostToolUse` never counts (a slow tool started in parallel
finishes while the menu waits), nor does a hook before Skuld knows the main conversation's
session id (a teammate's hook would look like the main agent's), nor any hook for a subagent's
card (a background subagent's menu can wait while the main agent works). Neither does a hook for
the card's own tool call, one within the render window after the card opened (parallel tool
calls), a subagent's or teammate's hook, or one that arrives after the broker stopped.

## Questions the CLI is not asking: reconcile (LEX-1979)

A question can be open in three places: the card above, the broker's pending and restored
`ask_user_question` lists (replayed to every client that connects), and the conversation
record (an AskUserQuestion `tool_use` with no `tool_result`). The CLI's session file
(`~/.claude/projects/<workspace>/<native id>.jsonl`) is the truth. Skuld checks every question
still open in the record or the restored list against it (an open card is otherwise left to
the checks above) at broker start, every sweep, and on
`POST /sessions/{id}/native-controls/reconcile` (owner or admin, audited):

| Session file says | Close | `dismiss_reason` |
|---|---|---|
| its `tool_result` is there | with that answer (`is_error: false`) | `resolved_in_terminal` |
| its `tool_use` is not there at all | `Dismissed: the CLI never showed it …` | `never_shown` |
| its `tool_use`, no result | kept while the pane shows a live menu or at broker start; otherwise `Dismissed: the CLI is no longer showing it.` | `menu_not_shown` |

An open card is checked too when its call is not in the file at all (LEX-2025): Claude writes
a main-agent question's call before its PreToolUse hook fires, so a card open for a sweep
interval whose call is still missing was never the session's question. It closes as
`never_shown` everywhere, even while the menu reads `unknown` (a dim prompt suggestion in the
composer reads as typed text). A card being answered, and any subagent's question
(its call is in the subagent's own file), are left to the card's checks.

Hooks from Claude Code's own side queries (prompt suggestions, speculation) never open a card,
a tool call or a turn: they carry an `agent_id` but no `agent_type`, send no SubagentStart and
write no transcript. Skuld logs each one (`claude_hook`) and counts it as
`side_query_hooks_ignored`. A real subagent names its type and is registered when it starts.

A question asked less than one sweep interval ago is not checked yet: its card may not be open,
nor its call written to the file. An unreadable session file closes nothing (409 from the route,
counted as `transcript_unavailable`).

Every close, by a check, a reconcile or an operator, is an `ask_user_resolved` frame with
`dismissed: true`, `control_kind: "question"` and the CLI `tool_use_id`. It clears the card and
the broker's lists, and writes the closing `tool_result` into the question's turn, in whatever
turn it is: live in the broker, and on every rebuild from the logged frame
(`niuu.domain.transcript_reducer.apply_question_closures`), so a restart that replays the
stored turns never brings the question back. The part carries
`metadata: {source: "skuld_dismissal", dismissed: true, dismiss_reason, request_id}`; a real
`tool_result` anywhere wins over a closure.

The dismiss route also takes a question with no card: pass its `tool_use_id` (or
`reconcile:<tool_use_id>`) as the request id. `GET …/native-controls` lists those as
`record_questions`, and `reconcile_counts` counts each close as
`closed.<trigger>.<reason>`, where the trigger is `reconcile_start`, `reconcile_sweep`,
`reconcile_owner` or `operator`.

`open_controls` on the session (REST and `session_updated`) carries each control's CLI
`tool_use_id` when known: `[{request_id, kind, opened_at, tool_use_id?}]`. A client shows a
question as waiting only while `open_controls` lists it.

## A question never holds a session (LEX-2016)

Claude Code's own question menu never traps a person, and the bridge does not either:

- **A partial answer submits.** `ask_user_answer` may answer some of the questions: each
  answer names its question (`question` text or `question_id`), and the optional `skipped`
  list, when sent, must name exactly the questions left out. Skuld moves past each skipped
  page with Right, as a person does, and presses Submit on the review page, which then warns
  "You have not answered all questions". Claude's result names only the answered questions,
  and that is the proof checked. An answer must cover at least one question; to answer none,
  send a message. Cards carry `metadata.requires_all_answers: false`.
- **A message closes a live question.** When a message arrives while a question card is open,
  Skuld picks "Chat about this" on the question page (Esc on the review page, which has no
  such row), waits for Claude's result for that tool call (hook or session file), closes the
  card with `dismiss_reason: closed_for_message`, and then types the message. After "Chat
  about this" Claude carries on in the same turn, so the message steers it; after Esc
  Claude ends its turn, Skuld closes that turn from the session file's `turn_duration`, and
  the message starts the next one. The delivery ACK is `delivered`.
  - The closing key is pressed once. If Claude has not confirmed the close in
    `question_result_wait_s`, delivery fails with `question_close_unconfirmed`, with nothing
    typed. A retry presses nothing and waits for the same proof, and the sweep closes the card
    once the proof lands. Meanwhile the card cannot be answered.
  - A permission prompt still refuses the message (below). So does a question card being
    answered, one with no CLI identity, or a screen that is not that question's menu.

## A message while a permission card is open: 409 `native_control_pending`

Skuld does **not** type a message into a live menu. In Claude's permission menu a digit
selects a row and Enter confirms the highlighted row, which is "Yes" (Allow), so pasting a
steer and pressing Enter would approve the pending command. The message is refused instead,
and nothing is typed. This happens when:

- a permission card is open after its delivery-time check (its menu is `live` or `unknown`),
  or a question card that cannot be closed as described above, or
- no card is open but a recognizable menu is `live` on screen (for example after an operator
  dismissed a card whose menu is still there). `control_request_id` is then empty.

Skuld does not retry the refusal. It emits the delivery ACK at once:

```json
{
  "type": "user_delivery_failed",
  "status": "failed",
  "id": "<msg id>",
  "request_id": "<message request_id>",
  "error": "Claude is waiting on a native control (tty-1-c3b257b9, permission, menu live); ...",
  "code": "native_control_pending",
  "control_request_id": "tty-1-c3b257b9",
  "control_kind": "permission",
  "control_opened_at": "2026-11-05T20:36:56.590000+00:00",
  "menu_state": "live"
}
```

followed by an `error` frame with `code=user_delivery_failed`, `reason_code=native_control_pending`
and the same control fields. The user turn is marked `steering_state=failed`.

`POST /api/v1/forge/sessions/{id}/messages` answers **409**, never `202 pending`:

```json
{
  "status": "failed",
  "delivery": "failed",
  "session_id": "5e4cea90-0595-4ec3-ac8a-7dbaf093e30d",
  "request_id": "<message request_id>",
  "sender": {"kind": "automation"},
  "detail": {
    "code": "native_control_pending",
    "message": "Claude is waiting on a native control (...)",
    "control_request_id": "tty-1-c3b257b9",
    "control_kind": "permission",
    "control_opened_at": "2026-11-05T20:36:56.590000+00:00",
    "menu_state": "live"
  }
}
```

`control_request_id` is `null` when no card is open. The message's request id is settled as
failed, so send again with a **new** `request_id` once the card is answered or dismissed.

## Listing and dismissing cards

Owner or admin only. A session credential (`forge_session` token) is refused, even with
grants; the routes are not on the session-token allow-list.

### `GET /api/v1/forge/sessions/{id}/native-controls`

Proxies the broker's `GET /api/native-controls` (owner room role):

```json
{
  "controls": [ { "request_id": "tty-1-c3b257b9", "control_kind": "permission", "...": "fields as in native_control_state" } ],
  "counts": {"dismissed.resolved_in_terminal": 3, "dismissed.menu_not_shown": 1, "transcript_unreadable": 0, "sweep_failures": 0},
  "sweep_interval_seconds": 30.0
}
```

`counts` holds dismissals by reason, transcript reads that failed (a transcript that outgrew
`tmux_question_transcript_max_bytes`, logged at error), and sweeps that failed (logged at
error; the sweep keeps running).

### `POST /api/v1/forge/sessions/{id}/native-controls/{request_id}/dismiss`

No body. Forge checks the caller may update the session (owner or admin), logs an `AUDIT`
line, and calls the broker's `POST /api/native-controls/{request_id}/dismiss` with
`{"dismissed_by": "<caller user id>"}`. The broker names the operator from its verified
identity header when it has one, else from that body. It logs
`AUDIT native control <request_id> dismissed by operator <principal> at <time> (kind, tool, tool_use_id, opened_at, menu_state)`
and emits `ask_user_resolved` with `dismiss_reason=dismissed_by_operator`.

| Status | Meaning |
|---|---|
| 200 | `{"status": "dismissed", "control": {<dismissal fields>}}` |
| 403 | Not the owner or an admin, or a session credential. |
| 404 | No such session, or no open card with that `request_id`. |
| 409 | The session is not running or not reachable, or its transport has no native controls. |

Dismissing only closes the card. If its menu is in fact still on screen, messages stay refused
with `native_control_pending` and an empty `control_request_id`; answer the menu in the
terminal.

## App guidance

- Show a card's age from `opened_at`. Show its menu state from `native_control_state`.
- For a card in state `unknown` that has been open for a long time, offer "Dismiss (resolved
  in terminal)", which calls the dismiss endpoint.
- Do not lock the steer box because a card exists. Send, and show the 409 /
  `user_delivery_failed` reason if Skuld refuses.
- A `person_only` card is urgent: Claude denies it after `auto_deny_after_seconds`.

## Settings (Skuld)

| Setting | Default | Purpose |
|---|---|---|
| `tmux_native_control_sweep_interval_s` | 30 | How often open permission and question cards are re-checked. |
| `tmux_native_control_absent_checks` | 3 | `gone` checks in a row, at most one per sweep interval, before a question card is dismissed (a permission card needs one). |
| `tmux_person_only_prompt_timeout_s` | 120 | Claude Code's auto-deny timeout, stated in the notification. |
| `tmux_question_transcript_max_bytes` | 1 MiB | The transcript read bound, shared with question answers. |
| `effort_confirm_settle_s` | 1 | How long a "Switch model?" / "Change effort level?" dialog must stay unchanged before the broker answers it (and re-answers one that ignored Enter). |
| `tmux_prompt_suggestions` | false | Claude's prompt suggestions in Forge-launched CLIs. Off sets `CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=0` for that CLI only (LEX-2026); `~/.claude/settings.json` is untouched. |

The menu render window is the existing `SKULD__TMUX_MENU_RENDER_WAIT_SECONDS` (1.5 s).

## Through the Guild facade

On a Guild host (Thor) the public `/api/v1/forge` is the Guild facade. It proxies
`GET /sessions/{id}/native-controls` and `POST /sessions/{id}/native-controls/{request_id}/dismiss`
to the session's owning Forge, which authorizes and audits; its answer is passed through. An owning
Forge without these routes answers 501 ("Update this session's Forge host"). A message refused for a
pending native control keeps its structured 409 `detail` (`code: native_control_pending`) through
the facade.
