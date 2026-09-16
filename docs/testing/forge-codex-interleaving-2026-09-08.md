# Codex message and tool interleaving: implementation and acceptance

Status: Forge `forge-2026.09.08.5` is deployed on the platform and all six
retained brokers. The physics history repair is applied and passed independent native-log,
database and simulator checks. Lexi build 2241 passed simulator acceptance and is available in TestFlight
for both iOS and macOS.

The backend release tag pins `596cacb54d60f649ebb9747f9e526b63cae74b0e`, with
source SHA-256 `1fdc9f20506fc230f2da6846aab11508a2d54287904991fadbd4eae2ea384924`.
The app candidate pins `20273018d831d2522f8b91594345a93739dbdc1a`.

## Incident and verified cause

The physics review exposed two coupled problems. Codex emitted distinct public
messages around tool calls, but the transport discarded message identity and
phase, and the reducer appended streamed prose only to the turn aggregate.
Completed database projections consequently contained tools without ordered
text parts. The iOS compatibility fallback placed the aggregate after those
tools. A subsequent live tool appended after that aggregate, and a canonical
refresh moved it back above the text. Markdown streaming also split structures
at blank lines inside code fences, lists, and tables.

The frozen physics evidence has 2,650 raw rows. Its first completed assistant
turn contains six native public messages totaling 7,127 characters and 42 tool
call/result pairs; its old projection contains zero text parts. The second
completed turn has six public commentary messages and likewise zero text
parts. Grouping retained frames by native thread and turn reconstructs both
turns with the exact original text bytes and every nontext dictionary intact.
This grouping matters because a queued human message arrived before the prior
native turn's terminal event.

A complete Claude public-frame replay additionally exposed an idle terminal
paint after a completed result. It reopened the reducer and appended the whole
scrollback as a duplicate response. Completion now closes that fallback until
new human or assistant activity; genuine terminal-only crash recovery remains
available. Twelve regressions cover the boundary. Reviewing six older Claude
corpus projections removed seven proven idle terminal ghosts while preserving
every real turn and every original capture byte.

The native import comparison also found an asynchronous question represented
as an `AgentMessage` with a tool-call identity. That is a control payload, and
must not be imported as a final answer. Public text extraction now excludes
that control form and foreign-thread events.

Private source evidence remains outside git under
`/home/thor/.niuu/validation/forge-interleaving-review-20260908`. Its immutable
manifest SHA-256 is
`8069d4b0db68b4eba064d57002ab4d3a4ed16e184bb18c236c25798a08c4d2eb`.
No private native analysis is included in the public fixtures or repair output.

## Message contract

The wire retains `type: "text"` and adds stable message identity. A public text
part carries `id`, exact `text`, `complete`, and, when available, native
`thread_id`, `turn_id`, `index`, and public `phase` (`commentary` or
`final_answer`). A fallback identity is explicitly synthetic.

1. Start reserves the message's position before subsequent tools.
2. Deltas append exact bytes to that identified message. Chunk boundaries do
   not insert whitespace or create messages.
3. Completion updates that same position with authoritative full text.
   Duplicate completions and late deltas cannot duplicate completed prose.
4. Stop carries final metadata and a byte count/SHA-256 receipt. If an oversized
   completion exactly matches text already streamed by the producer, the
   redundant full frame may be omitted. A client missing bytes requests the
   authoritative REST history; it never invents or silently truncates text.
5. Distinct identified messages use a paragraph separator only in the legacy
   aggregate `content`. Their individual text bytes remain unchanged. Legacy
   whole-message Claude behavior retains its historical separator.

The shared reducer is used by the broker, database replay, and CLI runtime.
ForgeKit retains legacy string text alongside typed identified text. App and
browser reconciliation preserve observed text/tool anchors, validate completed
text receipts, and reject late prior-turn events. Native interrupted fragments
remain visibly interrupted; a new native turn does not imply prior success.

## Repair and persistence

`niuu.domain.text_projection` accepts a legacy repair only when the original
turn identity, concatenated text bytes, and exact ordered nontext dictionaries
match the reconstructed candidate. Every message requires a captured completion
boundary and a unique valid identity. Gaps, conflicts, mismatches, missing
boundaries, and ambiguous legacy text preserve the original projection.

Repairs preserve timestamps, user messages, tools/results, accounting, and turn
IDs. An append-only `conversation.projection` marker binds the original and
replacement projection digests and source horizon. It is excluded from public
raw replay and consumed by cold reconstruction. The original raw ledger and
`conversation.turn` records are not rewritten. Reapplying a marker is a no-op.

The `projection_revision` envelope field is present on full, shallow,
incremental, and WebSocket histories. It is computed before pagination and
changes when an existing prefix is repaired, while remaining stable on normal
appends. Lexi cache v3 refetches rewritten prefixes even when turn IDs and the
incremental seam are unchanged. Draft storage is separate.

The read-only operator preview is:

```sh
python -m scripts.forge_text_projection \
  --ledger /private/export/database.json \
  --history /private/export/conversation.json \
  --native-rollout /private/native/rollout.jsonl \
  --native-id NATIVE_THREAD_ID \
  --output /private/new-repair-preview
```

It writes `history.json`, `marker.json`, and `summary.json` to a new private
directory. It verifies a contiguous single-session raw export, binds cache
projections to saved seeds, and enriches native IDs/phases only after exact
per-turn occurrence matching. Previewing performs no database or runtime writes.

Applying a preview requires a fresh session/cache/native backup, verification
that the target is idle and its broker has stopped, a transaction that checks
the current raw head before appending the derived marker, and atomic cache
replacement. Resume uses the same native identity. Acceptance compares the
original raw prefix byte-for-byte and checks live, stopped, cold, full, shallow,
and incremental history. Active user work must not be stopped for this repair.

The concrete PostgreSQL writer is `PostgresSessionEventLog.append_projection_repair`.
It locks the same session row as normal appends, requires stopped status and an
unchanged expected head, and verifies each replacement against durable seeds
and earlier repair markers. Exact retries are no-ops, including after resume.
Markers that JSONB scrubbing would change are rejected before insertion so the
stored marker cannot disagree with its validated digest.

A fresh private backup now contains all six retained sessions, their native
prefixes/cache/control state, and 432,917 raw rows. Its manifest SHA-256 is
`cd19c2a03ba3e88aa698baef59ed94c5e9925f5eecbea86fb0bd16ba5b15a3cf`.
The later read-only physics preview at raw head 4,410 reconstructs six
assistant responses with 25 individually verified native public messages. The
session has 12 total turns after new user activity since the first backup. All original text
bytes, nontext parts and turn IDs match. The guarded repair was subsequently
applied while the broker was stopped, then verified against the cold database
rebuild and resumed live history.

## Acceptance matrix

| Case | Required observation | Validation surface |
|---|---|---|
| I01 | Commentary → tool → commentary → final keeps native identity/order | Transport, reducer, Kit, app, browser, live canary |
| I02 | Arbitrary Unicode/Markdown chunks preserve exact bytes | Producer/reducer and exact UTF-8 client assertions |
| I03 | Adjacent message IDs remain distinct | Transport, Kit, model, browser |
| I04 | Completion without deltas appears once | Transport, reducer, importer, clients |
| I05 | Partial/full/repeated completion reconciles in place | All text consumers |
| I06 | Public native phase survives; private analysis does not | Transport/importer/reducer/Kit |
| I07 | Tool refresh and delayed result retain their anchor | Reducer, app, browser |
| I08 | Quiet polling cannot move a tool across prose | Model/browser regression and simulator observation |
| I09 | Reconnect/disconnected observer/reopen preserve order | Real sessions, snapshots, disk cache |
| I10 | Result and canonical handoff preserve row identity | Model tests and actual browser DOM test |
| I11 | Steering and late prior-turn events cannot mix spans | Transport guards and honest client fragment rollover |
| I12 | Cache/navigation/memory-warning paths preserve messages | Cache/model and simulator gates |
| I13 | Old projection repair preserves raw ledger and user IDs | Strict repair tests and guarded physics backfill |
| I14 | Native import and live capture agree on public items | Real native oracle and importer regressions |
| I15 | Claude whole-message and hook chronology remain correct | Existing shared gates and Claude tmux canary |
| I16 | Parallel/nested tools and agents retain attribution | Existing corpus, importer, transport/model suites |
| I17 | Fences/lists/tables/links survive streaming boundaries | Markdown tests and rendered UI checks |
| I18 | Large history/text stays bounded with explicit recovery | Frame serializer, receipt validation, lazy results |
| I19 | Legacy browser prose appears once; native prose interleaves | Component and isolated app/HTTP/WS integration |
| I20 | Legacy subprocess text has a valid structured wire shape | Transport and actual CLI runtime tests |

Each real canary must record its Forge/native IDs, source identity, prompt,
captured live frames, durable rows, REST history, and public native message
oracle. Provider wording is dynamic: compare observed identities, exact bytes,
tool pairing and chronology, rather than requiring one canned answer.
Use fresh owned workspaces and sessions. Faults and test prompts are confined
to those sessions. iOS simulator testing is sufficient; no physical-device
acceptance is required.

## Recorded validation

- Broad backend gate on production Python 3.11: 4,973 passed, 85.53% combined
  coverage against the unchanged 85% requirement. Four optional/unsupported
  cases skipped and one pre-existing live permission semantic case remains
  xfailed. Integration/live cases are separate lanes. The final gate includes both
  new interleaving fixtures and all twelve terminal-tail regressions.
- Real tmux gate: 57 passed, zero skips or xfails.
- Isolated PostgreSQL import/repair gate: 12 passed, zero skips or xfails.
- Final browser suite: 5,064 passed; branches 84.24% against the existing 84%
  requirement, statements 92.37%, functions 91.75%, lines 94.08%. Isolated
  app/HTTP/WebSocket Playwright integration passed.
- Transport/runtime focused gate: 351 passed; final Claude hook/tmux focused
  gate: 155 passed, one opt-in live CLI case deselected. A separate probe
  reconstructs 13 actual public native messages at chunk widths 1, 7, and 113
  with exact bytes, identity, phase, and order preserved.
- ForgeKit: 281 passed in 47 suites. iOS and macOS hosted tests: 123 passed each,
  zero failed or skipped. macOS test execution required invocation-only
  `ENABLE_APP_SANDBOX=NO` and empty test signing entitlements after the normal
  signed test host stalled in system sandbox initialization. Release source
  entitlements are unchanged; this is not normal macOS sandbox acceptance.

Both owned real providers completed commentary → command/result → commentary
→ command/result → final. The final Codex interval has 283 durable rows and 269
public frames; the final Claude interval has 320 durable rows and 306 public
frames. Captured live frames, database public projection and replay agree
exactly. All three messages and both command pairs match each provider's
public native oracle. Same-native-ID no-prompt resume preserved history.

The Claude investigation found PreToolUse can race both MessageDisplay and
visibility of earlier native JSONL. The bounded native-prefix recovery waits
up to 100 ms for exact same-session/tool/ancestry evidence, returning immediately
when available. Missing evidence retains captured hook order. The passing real
sample observed native visibility after 33.652 ms and 40.202 ms; text was emitted
before its tools in both cases. This does not infer chronology when evidence is
missing. Failed earlier no-wait captures remain separate evidence.

The actual Codex simulator live AX/layout capture observed text → running tool
→ completed tool → text → tool → final with stable tool coordinates. Those
newly streamed rows were below the retained viewport in the continuous video;
visible completed/reopen screenshots are separate evidence. A final build 2241
read-only replay then passed with 131 exact captured WebSocket payloads, 47
active REST reads reconstructed from recorded durable prefixes, visible command
completion, and exact final/reopened message order. Playback used 4× timing and
is recorded separately from actual native execution. The first proxy fixture
incorrectly served an idle REST snapshot during active frames; it is retained
as invalid harness evidence and excluded from acceptance.

The final 2241 simulator was restored to the normal Thor origin and also
showed the actual latest Claude canary in the expected interleaved order.
Only simulator testing was used; no physical phone is required.

Earlier failed runs remain in the private evidence: a collector used an
incorrect conversation URL; a hosted simulator result importer exhausted system
disk space; initial Python 3.13 found old SQLite resource warnings and six
expected corpus projection changes; installing optional OpenTelemetry on 3.11
exposed third-party deprecation warnings. Final backend verification uses an
isolated Python 3.11 environment matching production's absent OpenTelemetry.
All existing raw corpus captures and original capture reviews remain
unchanged; six Codex text projections and six Claude terminal-tail projections
have separate reviewed updates. Two newly captured interleaving scenarios
retain their complete observed intervals and public native oracle hashes.

## Deployed backend acceptance

The platform cutover preserved all eight broker processes then present. Each of
the six retained user brokers was separately checked idle twice, backed up,
stopped, and resumed with its original native identity. Every original ordered
turn ID remained present exactly once. The large agents-brain cache retained
all 89 turns; it was not discarded for a bounded cold rebuild. Agents-astra
also received two conservative automatic text enrichments whose original prose
and nontext parts were independently checked by the repair contract.

The physics repair now has six assistant responses with 25 native
public messages across 12 total turns. Its cache, append-only database marker,
cold reconstruction, and full/shallow/incremental HTTP histories agree. The
independent direct native-log oracle verifies all 25 public IDs/phases/text
bytes, and the 4,412 pre-repair rows remain exact before marker 4,413. All 139
original tool calls and 137 results remain unchanged. Two previously cancelled
responses retain their interrupted status; cold replay remains partial because
those original incomplete outcomes are preserved. The
revision is `text-items-1:e8f185f33f123241b9198976`. This is a repair of the
visible projection; original ledger entries and native transcript bytes remain
untouched. A post-rollout streaming comparison verified all 432,917 saved raw
rows and every frozen native-log prefix byte-for-byte across all six sessions.

Actual build 2241 cold-loaded the repaired physics session and retained its
12 ordered turn IDs and 25 exact native text items after a full app relaunch.
The cached revision matches, and the 166,724-byte cache has the same hash before
and after reopening. Screenshots and accessibility ordering verify commentary
between command groups in both the first and latest responses. No old physics
cache was present on this simulator before the check, so the actual observation
proves cold load/reopen; stale-revision invalidation is separately covered by
the focused cache tests.

The temporary main-process maintenance override has been removed. Normal
`Restart=always`, `KillMode=control-group`, and the health timer are restored;
the health check reports success. The two owned canaries are archived/stopped
with their completed histories retained. There are six remaining live brokers,
all on the new release.

The existing Vite viewer on port 5173 initially returned a cached module import
error. All 16 generated stylesheet hashes already matched the candidate. A
restart of only `niuu-forge-web.service` cleared that stale state: main module
and CSS requests return 200 and the served chat hook contains both new ordering
helpers. No tracked code or generated CSS was changed by this restart. The
candidate production browser build also passed. The API server's root remains
404 as before; this release does not introduce another browser hosting service.

Frozen private backend evidence is under
`/home/thor/.niuu/validation/forge-interleaving-20260908/backend-release-20260908T2252Z`.
Its 1,009-file manifest SHA-256 is
`cdae7ac3494dae94d47d8fb365c605217d575c02520f2ad3825d029f641c0ba2`.
All six pre-release and immediate pre-stop backups are under
`/home/thor/.niuu/backups/forge-interleaving-20260908`. Source and release tag are
pushed to the project repository. The browser/simulator evidence already frozen
under `/home/thor/.niuu/validation/forge-interleaving-20260908/` distinguishes
actual native live layout, exact captured-trace playback, and normal-origin
completed/reopened history checks.

## App release acceptance

At 2026-09-08 22:53:04 UTC, App Store Connect reports build 2241 as `VALID` and
`IN_BETA_TESTING` on both platforms, attached to the existing internal testing
group. iOS build ID is `a7350a78-0bf2-4880-a513-a00c73e1e4df`; macOS build ID is
`4e97b627-35e1-4d61-b8a5-cef41113c960`. The immutable `lexi-build-2241` tag pins
app source `20273018d831d2522f8b91594345a93739dbdc1a`.

Both archives, exports and Apple validation passed. All 579 compiled source
inputs and all 13 package pins match the tested candidate. Release entitlements
retain approved baseline parity; the macOS archive has its sandbox enabled and
debugger entitlement disabled. Signed artifacts and symbol UUIDs were verified.
The iOS IPA SHA-256 is
`a5f5275ef665c779aa4ac7794ea899df0289f88af825a73fb9bddede60d0d5a6`;
the macOS package SHA-256 is
`61d10920d1bd3acb50d4bf44eca616a908d8fce586b6f8e465a7ce39c637aa06`.
Original app workspaces and their unrelated local edits remain untouched.

Update Lexi through TestFlight to build 2241 and reopen the physics session.
The interleaved history is persisted on the server and remains available after
relaunch. Optional Markdown preferences are independent of this ordering fix.

## Optional structured-review wording

Ordering is a data contract and must not depend on model formatting. For a
review task, a user can additionally request:

> Keep progress messages brief and place them between tool batches. In the
> final answer, group findings by severity, cite the relevant file and line,
> explain the impact and proposed fix, and end with validation performed and
> unresolved questions. Use headings, bullets, and fenced code where useful.

Apply this as a task preference through the existing prompt/configuration
surface. Do not replace the provider's complete base instructions or infer
missing message boundaries from Markdown.
