# Forge live acceptance: first campaign, 2026-09-07

The new harness ran **8 real sessions and 44 scenario attempts** on the local
platform. Failures from earlier code and harness iterations are retained. All
created sessions were verified stopped after the campaign; existing user sessions
were left running.

The final Claude extended run passed **9/10 scenarios**. The final focused Codex
run passed **5/5**, including native agents and asynchronous questions. Both final
captures were compared directly with PostgreSQL and replayed using the production
replay implementation. The deployed public replay route still failed because the
shared platform process had not loaded the facade fix written during this review.

The evidence is summarized in [the machine-readable results](forge-live-results-2026-09-07.json).
The [acceptance plan](forge-live-agentic-acceptance.md) contains commands, exact
scenario contracts, review requirements, and the iOS matrix.

## Runtime and scope

- Branch: `skuld-agent-tokens`, base revision `ce7ee1ea781be2738548cbde77ebe7d3d525f17e`,
  with existing local work preserved and this review's changes uncommitted.
- Claude Code: `2.1.263`, model `claude-opus-5`, definition `skuldClaudeInteractive`.
- Codex CLI: `0.153.4`, model `gpt-6-astra`, definition `skuldCodex`.
- Actual Skuld processes launched through the running local Forge API. Real tmux,
  provider tools, worker agents, PostgreSQL storage, and session sockets were used.
- Legacy Claude SDK modes received no new live investigation; compatibility tests
  remain in the deterministic suite.

## Final action results

| Scenario | Claude final extended run | Codex evidence |
|---|---|---|
| Workspace read/command/write/read | Pass | Pass, final focused run |
| Native web search | Pass | Pass, preceding confirmation run |
| Two worker agents and parent join | Pass | Pass, final focused run |
| Failed command followed by recovery | Pass | Pass, preceding confirmation run |
| Markdown/Unicode presentation | Pass | Pass, preceding confirmation run |
| Reconnect and conversation recall | Pass | Pass, preceding confirmation run |
| Native plan/checklist | Failed: tool disabled/unavailable | Failed: tool unavailable |
| Explicit second-choice answer | Pass: Amber persisted | Pass: Amber persisted |
| Large Unicode and silent tool output | Pass | Pass, final focused run |
| Command completes while disconnected | Pass | Pass, final focused run |

Codex's preceding confirmation run used the same earlier core checks but still
exposed missing worker/question capture. The focused run retested those changed
paths. This table does not claim a single final Codex run passed all ten scenarios.

## Defects reproduced and changes made

### 1. Claude trust dialog consumed a chat prompt

The first Claude session showed `Accessing workspace` with `No, exit` selected.
The broker pasted a user prompt and sent Enter, closing the CLI. The log retained
the trust screen, delivery events, and `terminal_pane_closed`.

The tmux transport now rejects chat delivery and readiness detection while that
known trust dialog is present. The harness acknowledges only the exact synthetic
workspace it just created, then waits for the real prompt. Regression tests check
that no text is pasted and no parent turn begins at this gate.

### 2. Live steering event order differed from PostgreSQL

Both providers persisted their consumed-prompt event before `user_active` but
broadcast `user_active` first. These were byte-identical payloads in opposite order,
not a timestamp formatting difference. The broker now broadcasts the originating
consumption event before emitting its derived activation event.

The final captures pass the ordered live-to-database check for **1,064 Claude
frames** and **451 Codex frames**. Per-connect seeds are excluded explicitly and
snapshot reconciliation is separately reported.

### 3. Worker callbacks could end the parent's turn

Claude teammate Stop callbacks with a different native session ID closed the
parent turn and surfaced worker prose as parent prose. The early harness then sent
later prompts while the parent was still waiting for workers. This produced a
real mixed-instruction sequence that remains in the first extended trace.

Claude now recognizes child hooks using native session identity and explicit
sidechain/agent fields. Child stops cannot finalize the parent, and child display
callbacks cannot enter its prose stream. The harness also uses per-request
completion markers rather than treating any result as completion of the scenario.

Codex multiplexes worker notifications on the same app-server socket. Child-thread
notifications now remain scoped as `agent_event`, preserving their native payloads
without overwriting parent text, usage, thread identity, or turn completion.

### 4. Codex agent and question wire variants were uncaptured

The installed CLI's native rollout proved two real `spawn_agent` calls, even though
the initial Skuld log contained no agent receipts. This build emits
`subAgentActivity` items as well as supporting the standard `collabAgentToolCall`
schema. Both forms now map to paired spawn receipts and shared agent lifecycle
frames. The final run captured two started identities, two finished identities,
and 133 separately scoped child events.

The installed asynchronous question tool produces an `agentMessage` item with
`delivery=async` and question metadata. It differs from the standard blocking
`item/tool/requestUserInput` RPC. Both are now supported. The asynchronous answer
uses native steering; the blocking answer maps exact question IDs to its RPC
response. Missing, empty, and stale answers are rejected. Disconnect cleanup
settles questions and running-agent surfaces once.

The harness itself originally sent an outdated singular `answer` field. Its first
blue-answer probe was therefore insufficient proof of answer delivery. It now
sends the broker's actual `answers` array and deliberately selects **Amber**, the
second option. Both providers wrote the correct file in their final runs.

### 5. The public Forge facade lacked replay WebSocket forwarding

The local platform served `/sessions/{id}/log` but returned HTTP 403 for the
replay WebSocket handshake. The backing Volundr adapter had the route; the public
registry-backed facade exposed only the HTTP endpoints.

The facade now resolves the visible session owner and forwards replay sockets to
embedded or remote owners. It preserves authentication headers, query cursors,
visibility controls, frame order, and disconnect cleanup. Tests cover owner
visibility and the backing adapter's own authorization denial.

The shared running platform was not restarted during this campaign. Its old
process still rejects the public route; source-level and composition tests pass.
Deploy/reload this change and repeat the public replay check before calling that
production boundary green.

## Database and replay evidence

| Final capture | Raw PostgreSQL rows | Public wire frames | Full fixture replay | Mid-cursor replay |
|---|---:|---:|---|---|
| Claude `aba314d5-aafe-4487-afd1-b8306e121e6e` | 1,173 | 1,143 | 1,143 exact matches | 571 exact matches |
| Codex `a3082046-cd8f-4f8f-ba61-1502845b963a` | 525 | 511 | 511 exact matches | 255 exact matches |

The difference between raw rows and wire frames is the documented projection of
synthetic reducer seeds and per-connect handshakes. No gap/conflict sentinel or
foreign-session row was present in these final captures. PostgreSQL queries were
read-only and limited to the named test sessions.

## Reusable deliverables

The [reviewed corpus](../../tests/fixtures/forge-corpus/README.md) contains
**11 real scenario fixtures / 1,232 frames**, with source/export hashes, explicit
path redactions, scenario assertions, expected turns, checkpoint cursors, reviewer
notes, and the source run's remaining failures. Cases include both providers'
worker and question flows, failed-command recovery, formatting, large output, and
capture without an observer.

Each saved run also has an offline `review.html` Trace Lab. The final Codex viewer
was exercised in Chromium: scenario cards, agent filtering, cursor selection, and
payload search passed with no browser errors. The fixture server's catalog listed
all eleven cases; a real WebSocket replay delivered the 248-frame Codex agent case.

Private full bundles remain under `.forge-results/`:

- `live-probe/` — initial trust-gate failure and first Codex tool probe.
- `live-startup-fixed/` — first successful Claude tool probe.
- `live-core/` — first extended campaign, including worker contamination evidence.
- `live-confirmation/` — corrected Claude extended run and intermediate Codex run.
- `live-codex-native/` — final Codex native agent/question confirmation.

## Validation and remaining gates

- **4,326 backend tests passed**, 24 skipped for recorded dependency/capability
  reasons, and one existing expected failure. No backend test assertion failed.
- **21 corpus tests passed**, including exact wire playback and multiple cursors
  for every reviewed case, plus review/mutation guards.
- Targeted facade tests passed for embedded and remote forwarding, authorization,
  and disconnect cleanup. Python lint and `git diff --check` passed.
- The full Skuld coverage gate remains **red at 82.08% versus the required 85%**.
  The threshold was preserved. This is distinct from passing test assertions.
- Native plan tools are unavailable in these installed provider configurations.
  Mapping tests cover the protocol when a plan event exists; live plan acceptance
  is still red and no fabricated plan trace was promoted.
- The running public replay endpoint must be rechecked after loading the facade fix.
- iOS device/simulator rendering has **not** been run from this Linux workspace.
  The exported corpus and concrete matrix are ready for ForgeKit/LexiChat tests.
- Restart/resume, slow-client pressure, persistence outages, interruptions, and long
  soaks remain explicit follow-on live campaigns in the acceptance plan. Earlier
  deterministic fault coverage is not presented as real provider evidence for them.
