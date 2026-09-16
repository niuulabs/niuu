# Complete forge/dev-integration assessment — 16 September 2026

Pre-merge inventory, retained as historical context. See the [integration record](forge-dev-integration-20260916.md) for resolutions and validation. The references below describe the compared snapshots.

## What “since branching from dev” means here

This is not a single clean branch created last Friday. The source combines older Forge release and Ravn room development, then merges upstream dev on September 9. Its own reconciliation document confirms that the separately named `dev-integration` is the older June branch and `forge/dev-integration` is the new integration destination.

Pinned source: 6512419c730a14de117b438028beede00486e8e4. Pinned target: 242603e00ed55cdd2d916d903c9bc1a154b98b70.

The latest common ancestor is `629b4ca6f` (September 8), introduced into source by merge `b08bed86d` on September 9. The source-only graph also retains June–August history. Git does not record the original branch-creation event: an author-date cutoff or the latest merge-base date would omit inherited work. This report therefore inventories **all commits reachable from source but not current dev**, and inspects the **net source change relative to the latest common ancestor**.

- **200 source-only commit identities**, of which **193 non-merge commits and 7 merges**.
- **67 subjects appear in the body of our July 3 squash PR #818**, `b372860cf`. These describe already-integrated foundation work; they are not 67 missing changes. Current dev still has the shared transcript reducer, replay infrastructure, Grok transport and slash-command module.
- **8 pairs have exactly identical stable patch IDs within source history**. Another set of repeated subjects has context/patch differences; do not assume title equality means code equality.
- `git cherry` reports no exact patch equivalents against target. This does not detect the 67 commits folded into one squash; it is not evidence that everything is missing.
- After accounting for 7 merges, 67 squash-recorded subjects and 8 duplicate patch identities, **118 non-merge candidate patch identities remain**. This includes docs/tests and subsequent refinements, not 118 missing features or a ready-made cherry-pick list.
- Net source delta from common ancestor: **468 files, +149,154 / -2,195 lines**. Recorded fixtures account for **88,330 additions** across 40 files. `src` plus `web-next/packages` account for **152 files, +20,296 / -1,329 lines**, including packaged SQL and co-located web tests.
- The previously reported 40 September 11–16 commits are a subset of this inventory. The full branch does include significant web changes.

## Foundation already brought into dev

[PR #818](https://github.com/niuulabs/niuu/pull/818), July 3, records the June/early-July consolidation: Grok ACP support, slash-command discovery, replay-as-live WebSockets, activity/needs-input states and notifications, tmux steering and questions, live plans/subagents, durable event logging and a shared transcript reducer, persistence/reconnect/liveness work, conversation windows, present-file and catalog work. Earlier [PR #778](https://github.com/niuulabs/niuu/pull/778) brought reliability and context-restoring resume; [PR #968](https://github.com/niuulabs/niuu/pull/968) brought compact UX from the separate old branch.

These establish feature lineage, not proof that every subsequent source correction is present. The ledger marks squash subject matches explicitly; behavior still needs preserving when resolving overlapping changes.

## Full functional inventory to reconcile

“Outstanding delta” means concrete source additions/modifications beyond the common base that are still relevant against current dev. It does not mean the general feature never existed. Evidence is source inspection, targeted target-tree checks and history, not execution of each feature.

| Area | Incoming behavior / outstanding delta | Treatment |
|---|---|---|
| Claude live text and steering | Correct MessageDisplay wire shape; stream explanations between tools; correlate steering consumption; flush pending input without echoing completed answers; preserve native prompt identity and completion boundaries. | Integrate with current startup, flock and tmux changes. |
| Agent/tool observability | Real teammate names and finished detection; per-agent token usage and end timestamps; per-tool start/end/duration; stable current-turn start timestamp instead of resetting elapsed time on every activity transition. | New `agent_usage.py` and session `turn_started_at` are absent in target. Preserve existing activity and telemetry paths. |
| Images and file delivery | Shallow image hints, cached tool-result image previews, recovery from retained gateways, SendUserFile download endpoint; retain question inputs and tool descriptions during elision. | Existing present-file is already integrated; bring the additional preview/download/elision behavior rather than duplicate it. |
| Conversation performance | SSE-safe gzip, append-only `after`/`after_id` windows, stable indices and seams, tail retention under frame caps, recent history negotiation and Unicode-safe JSON projections. | New compression/projection helpers are absent in target. Preserve SSE streaming and authenticated instance routing. |
| Native session import and hydration | Import Claude/Codex native transcripts with provenance and idempotence; hydrate history before runtime startup; preserve native Claude identity rather than tmux names; protect retained caches. | New import readers/hydration modules are absent in target. Distinct from existing resume support. |
| Durable message delivery | Persist request identity/payload claims before dispatch; reject conflicting duplicates; retain uncertain delivery as pending rather than resending; preserve 202 through the facade. | New delivery ports, Postgres adapter and routes are absent in target. Requires schema and contention tests. |
| Codex process ownership/recovery | Owned app-server lifecycle, private control socket, native thread identity, orphan checks and Linux parent-death handling; larger native input frames with bounded browser output. | New `owned_codex_process.py` is absent. Reconcile with container/VM supervisors and existing auth renewal. |
| Claude questions and permissions | Validate current native menus, correlate answer acknowledgement with native results, handle free text/Unicode/mixed checkbox answers, stale questions and cancellation without false success. | Preserve existing question support and apply stronger consumption/identity contracts. |
| Native text/tool projection | Preserve native item identity and chronology, search input/results, durable tool results and terminal outcomes; repair completed public text consistently across live, disk and database views. | Shared reducer and transport changes; integrate server/client together. |
| Web chat | Ordered text/tool parts, canonical text repair, projection revisions, restoration of in-progress state, streaming Markdown fixes, room/thread rendering updates, launch model changes and Playwright regression cases. | Absent ordered-text/repair helpers confirm additional work beyond compact UX. Resolve conflicts with current simple-mode and shared-room UI. |
| Grok hardening | Correct model/runtime behavior, durable tool blocks, compatibility with older clients, avoid duplicate agent processes, close failed/timed-out turns on every exit path. | Grok exists in dev; these are additional fixes. |
| Muse transport | Muse MSP adapter, streaming/tools/approvals/questions, steering/resume and catalog/config entries. | New transport absent in target. Source docs are not proof that executable/auth/session-definition wiring is complete in every deployment. |
| PI transport | Native PI RPC lifecycle, ordered replay, answer translation, ended-question cleanup and fatal-stream teardown; capture/probe tooling. | New transport absent in target. Verify actual runtime binaries, credentials, config and backend packaging. |
| Reasoning/model catalog | Native effort levels/defaults and live controls, effort propagation through launch contributors, provider-derived catalog compatibility, historical model-default updates. | Reconcile capabilities with current catalogs; do not replay superseded model defaults in chronological order. |
| Ravn Anthropic compatibility | Filter unsupported tool-result keys and translate configured fixed-budget thinking for newer model families; align Bifrost adapter headers. | Target lacks the source helpers. Validate model capabilities rather than blindly adopt historical hard-coded assumptions. |
| Ravn room isolation | Child broker/member environment isolation, local base config, missing-provider-secret checks, member-owned memory paths, recall using the actual question rather than the room envelope. | Adapt to current shared collaboration, workload identity and credential architecture. |
| OpenClaw-facing gateway | New gateway/protocol/translation/store adapters and room bridge, session discovery/history/chat, RPC logging, native-client compatibility tests. | New adapters absent in target. This is phone/client protocol work, distinct from Flokk mesh and A2A. Review room delivery ownership against Skuld/Niuu boundary. |
| Memory retrieval | Bounded embedding inputs, suppress trivial episodes, expose unembedded corpus, optional Qwen reranking, memory ownership and recall-query fixes. | Useful intent, but source also introduces forbidden error-swallowing fallback behavior; revise before integration. |
| Local operator configuration | Travis/Neo personas, Lexi development config, local restart/start scripts and systemd unit. | Account for them explicitly as deployment-specific material; do not make these platform defaults. |
| Schema/bootstrap integrity | Transactional startup migrations under lock, immutable checksum ledger, historical filename/checksum aliases, Helm bridge, duplicate-index cleanup and migration-lineage tests. | Significant additional schema work. Adapt to our current migration catalog, not just Projects renumbering. |
| Persistent Projects | Git-backed identity, host registration/discovery, project session/parent references, idempotent dispatch, receipts/ack/export, CLI and repository-selected instruction snapshots. | Requires backend-visible checkout storage and integration with existing launch contracts. |
| Recent Codex/steering | Native protocol/command/approval/resume fidelity, active-turn steering, concurrent blocks, model/effort option discovery. | September 13 work previously listed; keep as coherent contracts with their tests. |
| Timeline and archive parity | Steering insertion chronology, immutable seed metadata, legacy archive compatibility and timed filesystem archive projection. | September 14 work; shared across all runtime hosts. |
| History protocol v2 | Bounded recent/older/refresh windows, cursor validation, lazy full-item reads, coherent snapshot/live boundary, typed recovery, preview identity and sender-only ingress. | Carry through gateway, API, aggregate, proxy and clients. Old-runtime limits remain explicit. |
| API/runtime preservation | Local backend identity, guarded API releases, preservation/reconnect tests and forwarding-task cancellation cleanup. | Reconcile with explicit backend settings, compute leases, VM recovery and warm pools. Local process tooling does not cover all deployment types. |
| Test/CI/evidence infrastructure | Stability/live workflows, trace/corpus/probe/validation tools, recorded native fixtures, PostgreSQL lineage/delivery tests, browser tests and rollout documents. | Bring relevant runnable tests; source reports do not establish our target's test results or cross-backend correctness. |

## Confirmed integration issues

### Five migration version collisions

| Version | Source migration | Current dev migration |
|---|---|---|
| 000062 | session_turn_started_at | pat_authority |
| 000063 | message_delivery_claims | topology_source_authority |
| 000064 | remove_duplicate_event_log_index | instance_tenant_attribution |
| 000065 | startup_schema_history | admin_settings |
| 000066 | forge_projects | compute_leases |

Current target continues through `000068_compute_profile_revision`. Allocate fresh numbers for incoming migrations as needed; do not change applied target migrations. Reconcile root SQL, packaged CLI SQL, Helm embedded SQL, checksum aliases, startup runner and bridge together. Source aliases describe its released history, not our authority/compute history. Test fresh databases and supported upgrade histories with real PostgreSQL.

### Memory fallback contradicts binding rules

Source changes in `src/niuu/adapters/search/sqlite.py` catch embedding errors, continue indexing without embeddings and fall back to FTS-only search. The Postgres adapter also catches embedding errors and stores unembedded rows. This contradicts `.claude/rules/no-fallbacks.md`. Keep valid input-budget/recall/ownership/reranking improvements, but preserve explicit failure when a configured capability is unavailable. A clean textual merge would silently introduce this regression.

### Deployment portability is not established by source validation

- Mini: project checkout access; same live gateway/native identity after API restart; preserve status and pending delivery.
- Docker: packaged runtime executables/auth; mounts and networking; distinct API/session-container lifecycle.
- OpenShell: supervisor/provider-profile ownership, storage and grant delivery; no accidental orphan cleanup; protocol access through sandbox routing.
- Kubernetes: migrations/config/chart bridge, PVC/checkout visibility, pod ownership/authentication, API rollout without destroying session pods.
- VM: compute leases, credentials, warm-pool profiles, controller crash recovery and interrupted stops; paths/context available inside guest; process ownership does not bypass the provider lifecycle.

For all five: launch, steering, approval/question, tool output, retry/delivery identity, reconnect, stop/start/native resume, archived transcript, history paging and owning-instance authorization. Apply runtime-specific tests to every supported runtime/backend combination; installing Python adapter code does not install the external runtime binary.

## Recommended integration approach

Use an isolated worktree at current remote dev, preserving the shared dirty checkout. Reconcile the **net branch delta**, with commit history and tests as evidence; do not cherry-pick all 200 identities or copy entire conflicting files.

1. Settle schema numbering/bootstrap compatibility and shared delivery/history primitives.
2. Bring native runtime recovery, Claude/Codex/Grok protocol and steering fixes with shared reducer and web projection changes.
3. Layer recent timeline/history-v2 work and backend-aware restart preservation onto that foundation.
4. Integrate Projects and native PI/Muse runtime packaging as separate reviewable slices.
5. Review Ravn/OpenClaw/memory changes separately for boundary and fail-hard compliance, retaining the requested functionality with compatible implementations.
6. Carry relevant tests and documentation, run unchanged repository gates, and prove the five deployment flows before claiming full integration.

The previous whole-branch merge simulation against 242603e00ed55cdd2d916d903c9bc1a154b98b70 found 23 conflicted files. More semantic conflicts exist in auto-merged files (the memory example above is concrete). This assessment is not a complete line-by-line code review or runtime validation.

## Complete 200-commit ledger

All source-only identities, in topological order. “Recorded in #818” is an exact subject match in that squash's commit body, not an individual patch-equivalence claim. “Candidate” means inspect/adapt, not automatically missing. Dates are authored dates and may precede their parent dates after rebasing.

| Commit | Author date | Classification | Subject |
|---|---|---|---|
| [6ac979005](https://github.com/xteo/niuu/commit/6ac97900576350af448fdb2841a9d7e43ae1c744) | 2026-07-03 | Candidate; same patch as 75d6031f2 | fix(forge): stream interleaved assistant prose live + confirm tmux steer submission |
| [8e9c35bab](https://github.com/xteo/niuu/commit/8e9c35babb0fa9580054fe693f090a043f8a8a7e) | 2026-07-03 | Candidate; same patch as 3c7c957f4 | fix(forge): MessageDisplay bridge speaks the real flush wire shape (turn_id/message_id/index/final/delta) |
| [f015a2a66](https://github.com/xteo/niuu/commit/f015a2a66c388c805f28cc6a9e7ec9f7b746c185) | 2026-07-03 | Candidate; same patch as c1ca60127 | fix(forge): flush uncorrelated steers to active at turn end (batched UserPromptSubmit) |
| [32f52616c](https://github.com/xteo/niuu/commit/32f52616c8a62e273d1d3daf9448048cc6f7dda0) | 2026-07-03 | Candidate; same patch as 411e35b51 | fix(forge): drop the late MessageDisplay flush that echoes the just-ended turn's result |
| [f65af2815](https://github.com/xteo/niuu/commit/f65af28157f400b1d0ef233ca9aa1cf8679b3cb2) | 2026-07-04 | Candidate | fix(forge): teammate panes get real names + dead panes stop accumulating |
| [21012b246](https://github.com/xteo/niuu/commit/21012b246b70716de709833b2b838939cdea0b37) | 2026-07-04 | Candidate; same patch as cdfd0f6d0 | fix(forge): capture the teammate "finished" signal so idle teammates stop accumulating |
| [1fef043cb](https://github.com/xteo/niuu/commit/1fef043cb8b96fe58f1b80f8d1cbb2a15c9b9074) | 2026-07-04 | Candidate; same patch as 1e1ed04af | feat(shallow): stamp image hint (is_image/mime/dims) on elided tool_result placeholders |
| [bbc379311](https://github.com/xteo/niuu/commit/bbc379311a931a86b2edbe147d117f3cb74d5d93) | 2026-07-05 | Candidate | feat(forge): server-side cached tool-result image previews (volundr layer) |
| [becd684b1](https://github.com/xteo/niuu/commit/becd684b1ff2d5cc22358829dc01fe02229f84d1) | 2026-07-05 | Candidate | fix(forge): stable turn anchor for RUNNING elapsed — no more intra-turn reset |
| [a4fc8cec7](https://github.com/xteo/niuu/commit/a4fc8cec78dcb81751a501d3c79d88743093816b) | 2026-07-05 | Candidate | fix(forge): recover tool-result previews from live history on old brokers |
| [3a456cb44](https://github.com/xteo/niuu/commit/3a456cb443b0ed8b14c0deadb4f75f2645aa4027) | 2026-07-10 | Candidate; same patch as 87932ed7a | fix(forge): thread turn_started_at through the ForgeService.update_activity facade |
| [a64798c7b](https://github.com/xteo/niuu/commit/a64798c7bfdc9935970146f030eaac4ddafffb11) | 2026-07-10 | Candidate | feat(skuld): serve SendUserFile attachments via download endpoint |
| [c240c83d0](https://github.com/xteo/niuu/commit/c240c83d0c84f8eec8a4a3f2269e1e97d8318331) | 2026-07-12 | Candidate | perf(api): SSE-safe gzip — conversation windows compress 4.3x on the wire |
| [a8ae38d9b](https://github.com/xteo/niuu/commit/a8ae38d9b243a92e86398284d27c69338479f97b) | 2026-07-12 | Candidate | perf(api): SSE-safe gzip on the ROOT platform app too (the served path) |
| [7e61540da](https://github.com/xteo/niuu/commit/7e61540dac5796935bef0d74dc77c7ad18b23c13) | 2026-07-12 | Candidate | feat(api): P2 — 'after' incremental conversation window (append-only cache fetch) |
| [748c0abc5](https://github.com/xteo/niuu/commit/748c0abc511bce54edbf58f3a6263e2576d592eb) | 2026-07-12 | Candidate | fix(api): stable conversation index space — FAULT-C tail-id gate + after_id seam echo + present_file live parity |
| [50e054438](https://github.com/xteo/niuu/commit/50e054438213c7fe7959f25745ac5308e360fed6) | 2026-07-12 | Candidate | fix(api): durable rebuild keeps the log TAIL under the frame ceiling + wedge-grace live timeout |
| [616928305](https://github.com/xteo/niuu/commit/6169283055abfb0682ae6b7313ad2d9534c9bd45) | 2026-07-13 | Candidate; same patch as 3632ca18f | fix(skuld): report claude's NATIVE session id as cli_session_id (tmux) — resume-restarts were silently losing conversations |
| [0cb4d94d5](https://github.com/xteo/niuu/commit/0cb4d94d525917c389654541a055c1d0ba02cad6) | 2026-08-02 | Candidate | feat(skuld): per-agent token usage + ended_at on /api/agents |
| [33af72adb](https://github.com/xteo/niuu/commit/33af72adbfa74dbdc19afa1851f8fc824427a868) | 2026-08-03 | Candidate | feat(forge): D1 per-tool timing on the conversation wire (live == durable) |
| [6a00c0367](https://github.com/xteo/niuu/commit/6a00c03674fc1368463ffeeafd7d49a51fc0dc23) | 2026-08-09 | Candidate | fix(integration): align replay cache and embedded migrations |
| [bdacd6a8a](https://github.com/xteo/niuu/commit/bdacd6a8ac8f1766dba36cd234c3c73bb94d5aa7) | 2026-08-10 | Candidate | fix(ravn): tool_result blocks must carry only the API's schema keys |
| [b392e7ed9](https://github.com/xteo/niuu/commit/b392e7ed902504bc6b6041e7164f2cdfca0f1bda) | 2026-08-10 | Candidate | fix(ravn): translate fixed-budget thinking to adaptive for current models |
| [7ca1603a7](https://github.com/xteo/niuu/commit/7ca1603a7f19ae681f3d1ee3b0bcb945c8420374) | 2026-08-12 | Candidate | test(ravn): Phase 0 — the wire evidence the OpenClaw shim will be built on |
| [4daf951ef](https://github.com/xteo/niuu/commit/4daf951ef324daad2c9226fc0861a4ecb524885e) | 2026-08-14 | Candidate | feat(ravn): speak OpenClaw, so Travis appears in the LexiChat iOS app |
| [0258cbd1c](https://github.com/xteo/niuu/commit/0258cbd1c7d63118c4d00ae505045e16e2d1bb34) | 2026-08-14 | Candidate | feat(ravn): log every OpenClaw RPC — the Phase-1 acceptance gate |
| [8d5022405](https://github.com/xteo/niuu/commit/8d50224053a8d7d248998136a82df11303beaf1e) | 2026-08-14 | Candidate | fix(ravn): a room's broker is configured by its own config, not its caller's |
| [0450d587d](https://github.com/xteo/niuu/commit/0450d587d17e15a9ba19396daa2f2988b2edb7d6) | 2026-08-14 | Candidate | feat(ravn): Neo — a second resident, built for the room |
| [b138e1470](https://github.com/xteo/niuu/commit/b138e14700c92338ff9a8dcabd197f317e7176f7) | 2026-08-14 | Candidate | fix(ravn): a room member is the agent this host runs, not a library default |
| [788ac160e](https://github.com/xteo/niuu/commit/788ac160ea1076dc4717b75fbfd128ed7145c2be) | 2026-08-14 | Candidate | fix(ravn): a room member runs its own config too, and says so when it cannot |
| [5b3301eaa](https://github.com/xteo/niuu/commit/5b3301eaa66d2bac8da45449b1930b12bb269a3f) | 2026-08-14 | Candidate | feat(ravn): a collaboration room becomes an OpenClaw session |
| [0ba2ad319](https://github.com/xteo/niuu/commit/0ba2ad31964176ae6c9401df502ac51b1b6b805d) | 2026-08-15 | Candidate | fix(memory): a long memory no longer costs an agent its turn |
| [5132b0c50](https://github.com/xteo/niuu/commit/5132b0c50acf1ef62cf66fab0a71dec44f4815e6) | 2026-08-15 | Candidate | fix(memory): a ping is not an episode |
| [c68278d61](https://github.com/xteo/niuu/commit/c68278d612ae19aa45493fa1cafa3cd71cf27241) | 2026-08-15 | Candidate | fix(ravn): a room member's memory is the member's own |
| [f48e2ea5f](https://github.com/xteo/niuu/commit/f48e2ea5f3a596fc15db1233a14b64d5f27ec171) | 2026-08-15 | Candidate | fix(memory): recall on what was asked, not on the envelope it arrived in |
| [620d1e869](https://github.com/xteo/niuu/commit/620d1e869144753db2dc4cc4c6315233b9efb251) | 2026-08-15 | Candidate | feat(memory): say how much of the corpus semantic search cannot see |
| [df1566e62](https://github.com/xteo/niuu/commit/df1566e6204a79477c22078c2a70ad8170153564) | 2026-08-15 | Candidate | feat(memory): rerank what recall found, against the question it was asked |
| [c13457366](https://github.com/xteo/niuu/commit/c134573666777d23ea50065a65ecc0d5864c1de6) | 2026-06-14 | Recorded in #818 | fix(session): advance last_active on real activity + emit activity_state on session_updated |
| [c043a0c32](https://github.com/xteo/niuu/commit/c043a0c32f6398630b8a6408b4fd2073f7d7474b) | 2026-06-17 | Recorded in #818 | feat(skuld): Grok Build coding sessions via ACP (Scaldy) |
| [3bac21dad](https://github.com/xteo/niuu/commit/3bac21dad8baa23dc4aa391b35398a7dda70f77b) | 2026-06-17 | Recorded in #818 | feat(skuld): discover and advertise session slash commands |
| [5e6fc04a7](https://github.com/xteo/niuu/commit/5e6fc04a73087b0db39ffb22d16d0d734df8561a) | 2026-06-19 | Recorded in #818 | docs(forge): design for session state observability and reporting |
| [b19295bc3](https://github.com/xteo/niuu/commit/b19295bc328b605c193bb7cc29b405433319a6c6) | 2026-06-19 | Recorded in #818 | feat(session): persist activity state and add awaiting_input attention state |
| [e168e35de](https://github.com/xteo/niuu/commit/e168e35dee6a2fa76ca5ea22ec80507e7f30eea2) | 2026-06-19 | Recorded in #818 | feat(session): emit needs-input as a first-class realtime and bus event |
| [6ba8da568](https://github.com/xteo/niuu/commit/6ba8da568537d7c898e3cf3c44b4be54dae5e4a0) | 2026-06-19 | Recorded in #818 | feat(skuld): report awaiting_input on human gates and add a progress heartbeat |
| [1c1b2ceb9](https://github.com/xteo/niuu/commit/1c1b2ceb9066d234aa80924e8793cc73bbb5332b) | 2026-06-19 | Recorded in #818 | feat(volundr): push notifications for sessions that need attention |
| [14bf40168](https://github.com/xteo/niuu/commit/14bf40168fe84a1062eb3cd49c952c51db2cc57a) | 2026-06-19 | Recorded in #818 | feat(web): render progressing and needs-attention session states |
| [dfae0541a](https://github.com/xteo/niuu/commit/dfae0541a06e989f7b5894ed6df7a9989a2cbfab) | 2026-06-19 | Recorded in #818 | docs(forge): mark session state reporting work as implemented |
| [1c765b499](https://github.com/xteo/niuu/commit/1c765b4991c6e702fe5ab3dbf62d01a8bf25df0b) | 2026-06-19 | Recorded in #818 | fix(skuld): real steering for tmux sessions via native CLI input |
| [e6f83011b](https://github.com/xteo/niuu/commit/e6f83011b95a62768ec5062f2025e77ea0164f51) | 2026-06-19 | Recorded in #818 | fix(skuld): steerable Grok ACP turns instead of stalling behind the lock |
| [3939cc937](https://github.com/xteo/niuu/commit/3939cc937e84df5202092d6f11aef774105cc97c) | 2026-06-19 | Merge | Merge branch 'lexi/forge-steering' into lexi/dev-api-integration |
| [c26f8e795](https://github.com/xteo/niuu/commit/c26f8e795a7cb88c7d712f499b464a96d7720b28) | 2026-06-19 | Recorded in #818 | feat(replay): replay-as-live WebSocket for recorded session frames |
| [72f8ebe77](https://github.com/xteo/niuu/commit/72f8ebe77040910c66ec456ddc0bf063ae5221ad) | 2026-06-19 | Recorded in #818 | fix(replay): graceful 1008 on unauth WS + honest read-only caps |
| [797f50b6f](https://github.com/xteo/niuu/commit/797f50b6f01175fa56d1522e0a9687ca6d91011c) | 2026-06-19 | Recorded in #818 | fix(replay): harden pacing + teardown; honest fixtures dir; deterministic tests |
| [03e474c42](https://github.com/xteo/niuu/commit/03e474c42ccf47549eba34ffde17aed16152ddd8) | 2026-06-19 | Merge | Merge branch 'lexi/forge-replay-as-live' into lexi/dev-api-integration |
| [c4c88e3aa](https://github.com/xteo/niuu/commit/c4c88e3aad03d17e6d82634a1df3bb3a0373ad75) | 2026-06-19 | Recorded in #818 | refactor(replay): apply code-review patches |
| [92222307d](https://github.com/xteo/niuu/commit/92222307ddd208f4fbbf866de755d214c6fbe452) | 2026-06-19 | Merge | Merge replay review patches (forge-replay-as-live) into dev-api-integration |
| [66407d2da](https://github.com/xteo/niuu/commit/66407d2da4a16d5437486e30189c154d6a39651b) | 2026-06-19 | Recorded in #818 | feat(replay): ship the 4 designed scenarios in the endpoint corpus |
| [5a63cde44](https://github.com/xteo/niuu/commit/5a63cde4446bb19a843e982f9c5dbe33bb4d85ee) | 2026-06-19 | Merge | Merge remote-tracking branch 'upstream/dev' into lexi/dev-upstream-merge |
| [305f99a5d](https://github.com/xteo/niuu/commit/305f99a5d553e6e917748f316cc181e45f13313e) | 2026-06-21 | Recorded in #818 | feat(tmux): bridge CLI-mode permission/question prompts to the structured ask_user_question protocol |
| [6446be1c4](https://github.com/xteo/niuu/commit/6446be1c401c2b246137052b58eb5bc1d94e8c70) | 2026-06-23 | Recorded in #818 | fix(broker): re-surface ask_user_question on reconnect + clear on resolve (tmux lock-state) |
| [67097ee26](https://github.com/xteo/niuu/commit/67097ee2640a3fc92dfc15cd02107d833f23e6fa) | 2026-06-23 | Recorded in #818 | feat(volundr): log 422 request-validation bodies + tolerant ActivityReport.metadata |
| [8f6b01a15](https://github.com/xteo/niuu/commit/8f6b01a158c022ef8879c24692d180afe065882c) | 2026-06-23 | Recorded in #818 | fix(forge): rebuild tmux/crash transcripts from the durable event log (Bug 2) |
| [8845f4bb4](https://github.com/xteo/niuu/commit/8845f4bb44a5ac6f586bbe8b47f3c1591142be32) | 2026-06-23 | Recorded in #818 | fix(forge): confirm + harden inbound steering after reconnect (Bug 3) |
| [5057c0920](https://github.com/xteo/niuu/commit/5057c09209dabe6972f86592bad0b03f1353dd0f) | 2026-06-24 | Recorded in #818 | feat(forge/tmux): Remote Control on Forge tmux sessions (default ON) |
| [e790d65df](https://github.com/xteo/niuu/commit/e790d65df1c4c2c35529304553ddfafb53fe015f) | 2026-06-24 | Recorded in #818 | fix(forge/tmux): always bind a live turn watchdog so interrupt→resume completes |
| [5d57054ff](https://github.com/xteo/niuu/commit/5d57054ffc6da54f8fdcd0a815f3fb22d83a6e73) | 2026-06-24 | Recorded in #818 | test(skuld): forge tmux test harness + steering/interrupt/crash-reconnect suites |
| [8570f9538](https://github.com/xteo/niuu/commit/8570f95383d463f3846be13d667dc8ac8d335f3e) | 2026-06-24 | Recorded in #818 | fix(forge/tmux): surface awaiting_input on questions, fix menu-render race + digit precedence |
| [f9b32652d](https://github.com/xteo/niuu/commit/f9b32652d38eb0e85f65a1c1a60bd6edf944d663) | 2026-06-24 | Recorded in #818 | test(skuld): ask_user_question (E) + permission-mode (F) suites; hermetic env |
| [e448cb3e2](https://github.com/xteo/niuu/commit/e448cb3e2ec43d620d80f943d489ba6a83187ee3) | 2026-06-24 | Recorded in #818 | test(skuld): cross-mode parity matrix + realign stale broker dispatch test |
| [28a1277e5](https://github.com/xteo/niuu/commit/28a1277e5918d4b121563f57e1f68ce07a439cf2) | 2026-06-24 | Recorded in #818 | test(skuld): custom tmux surfaces (G) + de-flake real-tmux smoke |
| [1e1946489](https://github.com/xteo/niuu/commit/1e1946489f8ac8a4005d2bb7b7e16e531f7e3d59) | 2026-06-24 | Recorded in #818 | docs(testing): record forge tmux test-plan outcomes (Phases 0-4 + bugs fixed) |
| [f2a75a111](https://github.com/xteo/niuu/commit/f2a75a111de8fa975bb28bcdab00f7d8881b5cab) | 2026-06-24 | Recorded in #818 | fix(volundr): strip NUL bytes before JSONB insert so /log never 500s |
| [d608e0f0d](https://github.com/xteo/niuu/commit/d608e0f0d23e2e2f2965c9bf7af5e6fd3a22b9b7) | 2026-06-24 | Recorded in #818 | feat(forge): runtime build/version identifier on Forge API + broker init |
| [0c757d480](https://github.com/xteo/niuu/commit/0c757d48059a52f28ed8963facc9253947121958) | 2026-06-25 | Recorded in #818 | feat(forge/tmux): surface Claude's live plan + running agents |
| [fe5fdcae3](https://github.com/xteo/niuu/commit/fe5fdcae35bf94b471703921a7a7cee8b573af72) | 2026-06-25 | Recorded in #818 | feat(forge/tmux): wire SubagentStart/Stop into the running-agents API |
| [7801e8e9b](https://github.com/xteo/niuu/commit/7801e8e9b01a778d60af9e563784d28c5dc95fc1) | 2026-06-25 | Recorded in #818 | fix(forge/tmux): map real Claude SubagentStart fields (agent_type as name) |
| [83dbd3226](https://github.com/xteo/niuu/commit/83dbd3226fffed8048ac60cd38ffdf28056e401f) | 2026-06-25 | Recorded in #818 | docs(forge): iOS handoff for the plan & running-agents API |
| [baa815102](https://github.com/xteo/niuu/commit/baa815102ec04c00114506727d7162db95938b82) | 2026-06-25 | Recorded in #818 | fix(forge/tmux): wait for REPL readiness before seeding the initial prompt |
| [487562d24](https://github.com/xteo/niuu/commit/487562d24c59d45c1d79a649ecd26b68a2a75b4b) | 2026-06-25 | Recorded in #818 | feat(forge/tmux): default Claude agent teams ON for interactive sessions |
| [3e846f21f](https://github.com/xteo/niuu/commit/3e846f21ffe2a8b0d0b2a50f7cffe8d2c57e02a6) | 2026-06-26 | Recorded in #818 | feat(forge): serve the in-progress turn + stamp subagent attribution (whole-truth) |
| [5a174857a](https://github.com/xteo/niuu/commit/5a174857aaf66b786a1aa8195efbc4967893e1e5) | 2026-06-26 | Recorded in #818 | fix(forge/tmux): stop the slash-command discovery probe from corrupting the booting REPL (instability B) |
| [3208f080c](https://github.com/xteo/niuu/commit/3208f080cd00d2e771ac2437a953cebd3546d9b0) | 2026-06-26 | Recorded in #818 | feat(forge/tmux): steering pending→active — correlate UserPromptSubmit, emit user_active, task-tracking prompt |
| [d1fd71466](https://github.com/xteo/niuu/commit/d1fd714664b8eb468c6ea1ffc9ff438e65c043c0) | 2026-06-26 | Recorded in #818 | feat(forge): steering pending→active for Codex + ALL non-tmux transports (fix stuck-pending regression) |
| [63995d605](https://github.com/xteo/niuu/commit/63995d605b641000484f5a114be075ea0f5934dc) | 2026-06-26 | Recorded in #818 | feat(forge): server-authoritative activity-state machine with state_since timestamps |
| [9479f011f](https://github.com/xteo/niuu/commit/9479f011f73ec968bee6c75cd11abf6267808f8e) | 2026-06-27 | Recorded in #818 | fix(volundr): harden event-log NUL sanitization (replace + text cols + retry) |
| [c4992a792](https://github.com/xteo/niuu/commit/c4992a792e361545ecb02b536a9c91ea1539bfe6) | 2026-06-27 | Recorded in #818 | fix(forge/codex): compaction retry reuses the original steer correlation (no FIFO orphan) |
| [5e9736ea2](https://github.com/xteo/niuu/commit/5e9736ea23961b49451a54080b18bbbedb2ef9c9) | 2026-06-27 | Recorded in #818 | test: make the suite hermetic against the dev-box's leaked platform env |
| [2457d8713](https://github.com/xteo/niuu/commit/2457d8713bb785b9ef1e5c4db970dc9f06244af2) | 2026-06-27 | Recorded in #818 | docs(forge): SRD for session persistence & live/database unification |
| [96ea379a1](https://github.com/xteo/niuu/commit/96ea379a1e7f3c27a4724420f268e41fa7c31707) | 2026-06-27 | Recorded in #818 | feat(forge): durable log is a complete superset of the live broadcast (Epic A) |
| [50604a3d8](https://github.com/xteo/niuu/commit/50604a3d8b5f9f2833406ffbc3fd43e9da81d05c) | 2026-06-27 | Recorded in #818 | feat(forge): pod-liveness reconciliation & truthful live-vs-db routing (Epic E) |
| [91a74d49b](https://github.com/xteo/niuu/commit/91a74d49b40ff6d175ade97b828ab28760968b34) | 2026-06-27 | Recorded in #818 | feat(forge): one shared transcript reducer — live fold == log rebuild (Epic B) |
| [14a754a5c](https://github.com/xteo/niuu/commit/14a754a5c4881585de49fa667ccc6d0e9c41aa24) | 2026-06-27 | Recorded in #818 | docs(forge): pin the chronicle as a derived, non-authoritative aggregate (Epic G) |
| [5f705f38b](https://github.com/xteo/niuu/commit/5f705f38b12b6fb545600670530abefe9dbdd183) | 2026-06-27 | Recorded in #818 | feat(forge): durable message delivery with log-reconstructable steering state (Epic C) |
| [5f906e358](https://github.com/xteo/niuu/commit/5f906e358d5f963f9f81317877327052c7255a61) | 2026-06-27 | Recorded in #818 | feat(forge): unify read paths — cold-read gate, mid-cursor history, reconnect cursor (Epic D) |
| [c507c827e](https://github.com/xteo/niuu/commit/c507c827e3c373c6a66be9e50714431aa20a11dc) | 2026-06-27 | Recorded in #818 | fix(forge): close durability & read-path gaps surfaced by the invariant suite (Epic F) |
| [a7905b79a](https://github.com/xteo/niuu/commit/a7905b79ad3ac17bcaf45c33cb4cc565d47e4d19) | 2026-06-27 | Recorded in #818 | test(forge): comprehensive cross-transport invariant suite, INV-1..10 (Epic F) |
| [8bac70078](https://github.com/xteo/niuu/commit/8bac700787de726dc7f7d884958f9d5d819d0903) | 2026-06-27 | Recorded in #818 | fix(forge): resolve adversarial-review findings — one fold, no read-path leaks, wired conflict detection (Epic H) |
| [85a0da505](https://github.com/xteo/niuu/commit/85a0da505567af61791fcdcf842cc3a119af8dc2) | 2026-06-27 | Recorded in #818 | docs(forge): adversarial validation report for the persistence unification (Epic H) |
| [a8b28c42d](https://github.com/xteo/niuu/commit/a8b28c42d648d52ddc285f43c879c00325373ebc) | 2026-06-27 | Recorded in #818 | fix(skuld): fully tear down tmux sessions on stop + resume-aware restart + stale-socket sweep |
| [334a0cb4c](https://github.com/xteo/niuu/commit/334a0cb4c35968ab53ce5fbf9542926b5f518bab) | 2026-06-27 | Recorded in #818 | test(forge): read the durable log before harness teardown in the D7 reconnect-cursor test |
| [5db8d9292](https://github.com/xteo/niuu/commit/5db8d92926fdbbabd8552d3c095d929f2815eed5) | 2026-06-28 | Recorded in #818 | fix(forge): persist activity_state — state_since facade + kill false-204 + FAULT C reconcile |
| [2bffdf4b9](https://github.com/xteo/niuu/commit/2bffdf4b93e60851835696e0dbef0550eeb1229c) | 2026-06-29 | Recorded in #818 | perf(forge): conversation read-path profiling + FAULT-C durable-count cache |
| [122d72661](https://github.com/xteo/niuu/commit/122d7266183bae7b96032d8f31d93932da37f577) | 2026-06-29 | Recorded in #818 | perf(forge): serve the conversation open optimistically; warm the durable count in the background |
| [0c9992dc8](https://github.com/xteo/niuu/commit/0c9992dc84915aac95843d819ce01a7d7e133e7e) | 2026-06-29 | Recorded in #818 | perf(forge): window the conversation to the last N turns (the transit fix) |
| [5e79d40e7](https://github.com/xteo/niuu/commit/5e79d40e7670ad4a50a6e16b161fd360ae48acdb) | 2026-06-30 | Recorded in #818 | fix(forge): stop the tmux durable rebuild double-counting every message (markdown + scrape twin) |
| [0783a957b](https://github.com/xteo/niuu/commit/0783a957b2ccb7f76999eb7ba4194fbed548561e) | 2026-07-01 | Recorded in #818 | feat(forge): present-file command — hand the user any host file as a durable in-app card |
| [385dde097](https://github.com/xteo/niuu/commit/385dde097dc7275865fbbac398d33a1239c3de02) | 2026-07-01 | Recorded in #818 | docs(forge): advertise present-file to agents — system-prompt hint + skill |
| [9c04e3cfe](https://github.com/xteo/niuu/commit/9c04e3cfe40328fe46cad68beb87fd27ef3714c1) | 2026-07-03 | Recorded in #818 | feat(bifrost): curate model catalog + GPT-5.6 Sol Ultra effort |
| [75d6031f2](https://github.com/xteo/niuu/commit/75d6031f246cf15361d89242fb8e3778d37ff962) | 2026-07-03 | Candidate; same patch as 6ac979005 | fix(forge): stream interleaved assistant prose live + confirm tmux steer submission |
| [3c7c957f4](https://github.com/xteo/niuu/commit/3c7c957f4a7da6392da01aef472173b279b6d1f3) | 2026-07-03 | Candidate; same patch as 8e9c35bab | fix(forge): MessageDisplay bridge speaks the real flush wire shape (turn_id/message_id/index/final/delta) |
| [c1ca60127](https://github.com/xteo/niuu/commit/c1ca6012768c15945fba2c1cf0bc9463cea9d5d7) | 2026-07-03 | Candidate; same patch as f015a2a66 | fix(forge): flush uncorrelated steers to active at turn end (batched UserPromptSubmit) |
| [411e35b51](https://github.com/xteo/niuu/commit/411e35b51608bc12b3bde2df4f6e517e97d46f24) | 2026-07-03 | Candidate; same patch as 32f52616c | fix(forge): drop the late MessageDisplay flush that echoes the just-ended turn's result |
| [89abd47e1](https://github.com/xteo/niuu/commit/89abd47e1cfdd80bea33a700c30c2c8eb9b86e46) | 2026-07-04 | Candidate | fix(forge): teammate panes get real names + dead panes stop accumulating |
| [cdfd0f6d0](https://github.com/xteo/niuu/commit/cdfd0f6d09a2c2934dfa325d956fd1e9756b2cdc) | 2026-07-04 | Candidate; same patch as 21012b246 | fix(forge): capture the teammate "finished" signal so idle teammates stop accumulating |
| [1e1ed04af](https://github.com/xteo/niuu/commit/1e1ed04afeeacf5333bddacb948c390f23842a8c) | 2026-07-04 | Candidate; same patch as 1fef043cb | feat(shallow): stamp image hint (is_image/mime/dims) on elided tool_result placeholders |
| [6d858ce28](https://github.com/xteo/niuu/commit/6d858ce28712f739224191a28852039432cd9bd2) | 2026-07-05 | Candidate | feat(forge): server-side cached tool-result image previews (volundr layer) |
| [3d2586745](https://github.com/xteo/niuu/commit/3d2586745bd3984d83738b3d19ad628ab2e98482) | 2026-07-05 | Candidate | fix(forge): stable turn anchor for RUNNING elapsed — no more intra-turn reset |
| [93ab07c1b](https://github.com/xteo/niuu/commit/93ab07c1bd23543d7ab53d3a549ad1b265f79aea) | 2026-07-05 | Candidate | fix(forge): recover tool-result previews from live history on old brokers |
| [87932ed7a](https://github.com/xteo/niuu/commit/87932ed7a120fba1ce179d61044bbcbcd288055d) | 2026-07-10 | Candidate; same patch as 3a456cb44 | fix(forge): thread turn_started_at through the ForgeService.update_activity facade |
| [e0d8ccd75](https://github.com/xteo/niuu/commit/e0d8ccd7543c86bc959d492f5d8efa31437e197e) | 2026-07-10 | Candidate | feat(skuld): serve SendUserFile attachments via download endpoint |
| [eb38e008a](https://github.com/xteo/niuu/commit/eb38e008a9924f9d449bb24583bb4bb14c1d515e) | 2026-07-12 | Candidate | perf(api): SSE-safe gzip — conversation windows compress 4.3x on the wire |
| [45edc1f1d](https://github.com/xteo/niuu/commit/45edc1f1d039073ad6a0cc4426ece71dae776032) | 2026-07-12 | Candidate | perf(api): SSE-safe gzip on the ROOT platform app too (the served path) |
| [9add6feeb](https://github.com/xteo/niuu/commit/9add6feeb47244753ed710d5850d397f0944b99c) | 2026-07-12 | Candidate | feat(api): P2 — 'after' incremental conversation window (append-only cache fetch) |
| [fcef12857](https://github.com/xteo/niuu/commit/fcef12857c44fba8eb40bc0d64689162c63f4f04) | 2026-07-12 | Candidate | fix(api): stable conversation index space — FAULT-C tail-id gate + after_id seam echo + present_file live parity |
| [0f8348d46](https://github.com/xteo/niuu/commit/0f8348d4644b7bd360a0648f203bcd321ffbd37b) | 2026-07-12 | Candidate | fix(api): durable rebuild keeps the log TAIL under the frame ceiling + wedge-grace live timeout |
| [3632ca18f](https://github.com/xteo/niuu/commit/3632ca18f0305b365ae5e1b24b840d8315209204) | 2026-07-13 | Candidate; same patch as 616928305 | fix(skuld): report claude's NATIVE session id as cli_session_id (tmux) — resume-restarts were silently losing conversations |
| [2c86b28da](https://github.com/xteo/niuu/commit/2c86b28da56e2acf6ef0ae3bad50f904a9b51df6) | 2026-08-02 | Candidate | feat(skuld): per-agent token usage + ended_at on /api/agents |
| [8ce5bf3b5](https://github.com/xteo/niuu/commit/8ce5bf3b5d7c29b47e870e83d2c19d1a39081702) | 2026-08-03 | Candidate | feat(forge): D1 per-tool timing on the conversation wire (live == durable) |
| [6de7da3a6](https://github.com/xteo/niuu/commit/6de7da3a698425a5418694086e8d7db1341ba1c0) | 2026-08-11 | Candidate | fix(forge): never elide an AskUserQuestion input — it is the UI, not detail |
| [5834ebe45](https://github.com/xteo/niuu/commit/5834ebe4559b1d8f0c01c785584a184a150568eb) | 2026-08-15 | Candidate | fix(grok): Grok end-to-end — a dead model id and a promise we never kept |
| [974f45d3d](https://github.com/xteo/niuu/commit/974f45d3d14b90df97bee8755c54f4a564d54169) | 2026-08-15 | Candidate | fix(grok): tool blocks reach the durable transcript — Codex/Claude parity |
| [48ed2d218](https://github.com/xteo/niuu/commit/48ed2d21866ccd1153d0617959e8d31a558e9d51) | 2026-08-15 | Candidate | fix(codex): tool results reach the durable transcript, and every row closes |
| [e378f98a8](https://github.com/xteo/niuu/commit/e378f98a8d49cd07d19d6629b4f6cfddc7cccf19) | 2026-08-15 | Candidate | feat(codex): gpt-5.6-sol is the Codex default model |
| [c812cdfdb](https://github.com/xteo/niuu/commit/c812cdfdbee3ba4dbb0b2884d0f80b708bd33f96) | 2026-08-15 | Candidate | feat(claude): Opus 5 is the default for both Claude modes |
| [ad2a63387](https://github.com/xteo/niuu/commit/ad2a6338794a11e1a7b491049bf22830f34c08eb) | 2026-08-16 | Candidate | fix(grok): survive a client on an old build, and never spawn two agents |
| [5a4cbddc7](https://github.com/xteo/niuu/commit/5a4cbddc7a7ab8fc6f74e167191e1dcf7c98398d) | 2026-08-16 | Candidate | fix(grok): a timed-out turn must END, not strand the session |
| [e4daf106c](https://github.com/xteo/niuu/commit/e4daf106c1c387c9d3e770b98aae0af95ac2c405) | 2026-08-17 | Candidate | fix(grok): a turn can no longer be left open by ANY exit path |
| [b5c9ca78e](https://github.com/xteo/niuu/commit/b5c9ca78ecc28daf8bc907d48729a43a64aa13e7) | 2026-08-19 | Candidate | feat(skuld): keep the model's tool description through input elision |
| [ce7ee1ea7](https://github.com/xteo/niuu/commit/ce7ee1ea781be2738548cbde77ebe7d3d525f17e) | 2026-09-05 | Candidate | feat(codex): GPT-6 Astra is the Codex flagship and default; Terra removed |
| [11c4acc2b](https://github.com/xteo/niuu/commit/11c4acc2b7ea17c436fbfe8993a68e53ab4d6e83) | 2026-09-08 | Candidate | fix(forge): harden native recovery, delivery and replay across clients |
| [9f2a3b2e2](https://github.com/xteo/niuu/commit/9f2a3b2e2389d9422b540f07160ae3074ad213a3) | 2026-09-08 | Candidate | fix(forge): preserve native search and harden recovery controls |
| [fa1bdde80](https://github.com/xteo/niuu/commit/fa1bdde8095088167bbac93c7038573baff501ca) | 2026-09-08 | Candidate | fix(forge): confirm Claude answers from native results |
| [fe3ea8c92](https://github.com/xteo/niuu/commit/fe3ea8c92cf9be41ee4d1bb1196bd3a27e194cfd) | 2026-09-08 | Candidate | fix(forge): preserve mixed checkbox custom answers |
| [596cacb54](https://github.com/xteo/niuu/commit/596cacb54d60f649ebb9747f9e526b63cae74b0e) | 2026-09-08 | Candidate | fix(forge): preserve native message and tool interleaving |
| [103f14757](https://github.com/xteo/niuu/commit/103f14757179a4f222dac6f3a146a58699a64b01) | 2026-09-08 | Candidate | docs(forge): record interleaving release and repair acceptance |
| [8278c4b90](https://github.com/xteo/niuu/commit/8278c4b9020a10f7da3f9bc6f6cae2e0dc5cb6d8) | 2026-09-09 | Merge | merge(forge): reconcile released session stability with integration |
| [b08bed86d](https://github.com/xteo/niuu/commit/b08bed86d456474373a4f35e35412224035f4396) | 2026-09-09 | Merge | merge(forge): integrate reviewed upstream runtime changes |
| [432192e71](https://github.com/xteo/niuu/commit/432192e713567868066a3b26bad6f3c698e06306) | 2026-09-09 | Candidate | fix(forge): close validation gaps in reconciled session lifecycle |
| [062352536](https://github.com/xteo/niuu/commit/062352536eab2ed50d497781949a0fcdf77105f7) | 2026-09-09 | Candidate | fix(testing): close owned resources and gate migration lineages |
| [75da60061](https://github.com/xteo/niuu/commit/75da60061f4fcc015c65af2bf3730302cb3eacc4) | 2026-09-09 | Candidate | docs(forge): record reconciliation and simulator acceptance |
| [db81cb9cc](https://github.com/xteo/niuu/commit/db81cb9cc44cc8e83ed8b04a2ba91bc9759bc399) | 2026-09-09 | Candidate | test(forge): begin PI RPC capture and acceptance workflow |
| [953775601](https://github.com/xteo/niuu/commit/9537756015814840d15c8fe7f202d5c5376696f8) | 2026-09-09 | Candidate | test(forge): cover PI probe lifecycle and restart failures |
| [75233fcf7](https://github.com/xteo/niuu/commit/75233fcf711954ef72995524d15d6848a9b5380d) | 2026-09-09 | Candidate | feat(forge): integrate native PI RPC sessions and ordered replay |
| [ad9d2c4f3](https://github.com/xteo/niuu/commit/ad9d2c4f3cc6312baad9833da845baa3b7d64fe2) | 2026-09-09 | Candidate | fix(forge): translate iOS PI answers and clear ended questions |
| [6b353d5e3](https://github.com/xteo/niuu/commit/6b353d5e38891406d1311224ea5dab6dd41c1189) | 2026-09-09 | Candidate | fix(forge): stop PI processes after fatal RPC stream failures |
| [d266a59f2](https://github.com/xteo/niuu/commit/d266a59f2fa3d955fc90a917846e8ed1acb06ae5) | 2026-09-09 | Candidate | feat(forge): preserve and control native reasoning effort |
| [ca456289c](https://github.com/xteo/niuu/commit/ca456289c5caa8ddb60bcb13ca246a4f40e68b2d) | 2026-09-09 | Candidate | fix(bifrost): retain provider-derived catalog compatibility |
| [27b92e051](https://github.com/xteo/niuu/commit/27b92e0516d26ec51bf56af433bab818cbf23037) | 2026-09-10 | Candidate | fix(forge): load bounded recent activity with on-demand history |
| [891cb42bc](https://github.com/xteo/niuu/commit/891cb42bcf7a73d854f31d5d31ae001aa349142a) | 2026-09-10 | Candidate | fix(forge): render incomplete Unicode safely in history projections |
| [f44f62d5d](https://github.com/xteo/niuu/commit/f44f62d5d309aa8f233744dd2a44f53cb04b2c8b) | 2026-09-10 | Candidate | fix(forge): forward recent replay negotiation through session proxy |
| [b10bea089](https://github.com/xteo/niuu/commit/b10bea0896b4d3fdf46c64e7bb7d23532058753a) | 2026-09-11 | Candidate | docs(forge): propose persistent projects and coordinated sessions |
| [673b50004](https://github.com/xteo/niuu/commit/673b50004fd67f8e8b9256cc07893b9f4b09bfc6) | 2026-09-11 | Candidate | feat(forge): add persistent Git projects and coordinator CLI |
| [49ed405f5](https://github.com/xteo/niuu/commit/49ed405f573b65aee612af8ca4ff3f003c7f67e1) | 2026-09-11 | Candidate | docs(forge): record project deployment and live acceptance |
| [44f85dc32](https://github.com/xteo/niuu/commit/44f85dc3230852d1724b0e52d7a07e0f7698f8ae) | 2026-09-12 | Candidate | feat(forge): connect projects from an existing host checkout |
| [c6d80980c](https://github.com/xteo/niuu/commit/c6d80980c304f972461d9e31d36ac3aeca35cc8d) | 2026-09-12 | Candidate | docs(forge): describe checkout-based project setup and validation |
| [f0ead42d5](https://github.com/xteo/niuu/commit/f0ead42d569e2cfbf9cfe7df8d5904e0e92b87c8) | 2026-09-13 | Candidate | fix(skuld): preserve public text across interleaved tool blocks |
| [c8a8fd189](https://github.com/xteo/niuu/commit/c8a8fd189556ad907129cfe919a2cf3c455fd16d) | 2026-09-13 | Candidate | fix(skuld): preserve Codex protocol fidelity and fail closed |
| [2e8afea3b](https://github.com/xteo/niuu/commit/2e8afea3ba548d00e140add54e22f5fbdd21f286) | 2026-09-13 | Candidate | fix(skuld): expose native Codex command semantics and errors |
| [1037512df](https://github.com/xteo/niuu/commit/1037512df98e44ec2f3ac87b5f63ff1092e75e06) | 2026-09-13 | Candidate | fix(skuld): keep Codex raw tool events observational |
| [a5acb7c02](https://github.com/xteo/niuu/commit/a5acb7c02a3a5d6fc2be9732e0b11fe28660a87d) | 2026-09-13 | Candidate | fix(steering): honor live input without interrupting active turns |
| [631989f17](https://github.com/xteo/niuu/commit/631989f174e668bbf38cfbed1724ec2509a28de9) | 2026-09-13 | Candidate | feat(projects): snapshot repository-selected instruction files |
| [11e9e0c66](https://github.com/xteo/niuu/commit/11e9e0c668747fd0af1b97463f2c238e9fb40811) | 2026-09-13 | Candidate | refactor(skuld): leave task workflow to project instructions |
| [087050587](https://github.com/xteo/niuu/commit/087050587fa9efb7c3c58d2bc5f2fe71594efa4d) | 2026-09-13 | Merge | merge: compose pinned project context and live steering fixes |
| [3fe19499a](https://github.com/xteo/niuu/commit/3fe19499a441bf5a1b634d36bdf93356b69d933d) | 2026-09-13 | Candidate | test(skuld): record combined Codex integration review evidence |
| [3cfa0e9cb](https://github.com/xteo/niuu/commit/3cfa0e9cb681eb3c4c9c87ba604955859a31f235) | 2026-09-13 | Candidate | fix(skuld): honor native Codex approval and resolution contracts |
| [a75ba536f](https://github.com/xteo/niuu/commit/a75ba536f6ed6e51db4cb6761b5a48ffdd55c189) | 2026-09-13 | Candidate | fix(skuld): require native resume identity and current history params |
| [7c6cea222](https://github.com/xteo/niuu/commit/7c6cea222758ae52aa08711b16ed929c51687449) | 2026-09-13 | Candidate | docs(skuld): checkpoint Codex approval and replay contract validation |
| [4e6154ebf](https://github.com/xteo/niuu/commit/4e6154ebf65b25d368cae1d5b64003fa11ba14f2) | 2026-09-13 | Candidate | fix(skuld): scope concurrent Codex blocks and native turn outcomes |
| [e4b88cdd4](https://github.com/xteo/niuu/commit/e4b88cdd4e55084410053982ad3e81eae835f883) | 2026-09-13 | Candidate | feat(skuld): discover and persist native Codex runtime options |
| [72f8fdbe0](https://github.com/xteo/niuu/commit/72f8fdbe0389a1568fed7d739e60d9fc39c1991e) | 2026-09-13 | Candidate | docs(forge): record native Codex proof and deferred command design |
| [c658b1d88](https://github.com/xteo/niuu/commit/c658b1d88ee6fad4df5cf2f8396573aba51da93a) | 2026-09-13 | Candidate | fix(forge): preserve local gateways during startup reconciliation |
| [a056420ad](https://github.com/xteo/niuu/commit/a056420ad9a0a41e153e370c135efc8cf2ee1443) | 2026-09-13 | Candidate | docs(forge): preserve rollback impact and isolated recovery evidence |
| [3dd80b79d](https://github.com/xteo/niuu/commit/3dd80b79d7e5f2a896af1ca92240acb9852de235) | 2026-09-13 | Candidate | fix(forge): prepare fail-closed local API releases with restart proof |
| [f5d7a5e49](https://github.com/xteo/niuu/commit/f5d7a5e49eafd765f8daf53bdc51c95ac1949939) | 2026-09-13 | Candidate | test(forge): model websocket query parameters in reconnect harness |
| [5ac49d531](https://github.com/xteo/niuu/commit/5ac49d53186a7a1209a7f5c72477b74f62e7327a) | 2026-09-13 | Candidate | docs(forge): record staged Codex release and preservation evidence |
| [755eca402](https://github.com/xteo/niuu/commit/755eca402a2b942a7dae0c6176d70f0101df2ebe) | 2026-09-13 | Candidate | docs(forge): record Spark deployment and preservation evidence |
| [273e9aa32](https://github.com/xteo/niuu/commit/273e9aa328521ba79944b79866c7b4d09ed8ce34) | 2026-09-14 | Candidate | fix(skuld): preserve steering chronology across replay |
| [730933bb1](https://github.com/xteo/niuu/commit/730933bb173c81ddcb35fdbdf52be725eacb2c49) | 2026-09-14 | Candidate | fix(skuld): keep legacy archived steering projections unchanged |
| [39da0146a](https://github.com/xteo/niuu/commit/39da0146a2957f81f51bad54c107d7b042e55cd2) | 2026-09-14 | Candidate | fix(skuld): avoid mutating retained transcript seed metadata |
| [6085e6c06](https://github.com/xteo/niuu/commit/6085e6c0655387ab8acd407c6eec00fb6f1e0f16) | 2026-09-14 | Candidate | docs(forge): record verified Spark steering timeline rollout |
| [233ea5c9e](https://github.com/xteo/niuu/commit/233ea5c9ea2e916009f0e71d5f4e9718e0859f0a) | 2026-09-14 | Candidate | docs(forge): preserve post-cutover Spark stabilization evidence |
| [650088f3e](https://github.com/xteo/niuu/commit/650088f3e2fe234e75cd7d2578acae911290ee91) | 2026-09-14 | Candidate | fix(volundr): project timed filesystem archive replay |
| [45f0a4940](https://github.com/xteo/niuu/commit/45f0a49409d08e36563d0a5efc69bbcd54ae751e) | 2026-09-14 | Candidate | docs(forge): detail live deduplication and archive replay gaps |
| [21bb0de6e](https://github.com/xteo/niuu/commit/21bb0de6ec0ec467865382dd99640cf37b6cffdb) | 2026-09-14 | Candidate | docs(forge): record Spark archive replay rollout and native handoff |
| [64b95f67d](https://github.com/xteo/niuu/commit/64b95f67dea0830ee809faf6fad1139b0c8e8f13) | 2026-09-14 | Candidate | docs(forge): hold Thor upgrade under zero-disruption condition |
| [2712ba282](https://github.com/xteo/niuu/commit/2712ba282f58eb834005a6e340302b900395c636) | 2026-09-14 | Candidate | fix(skuld): bound history delivery and fence replay recovery |
| [b5f50ec90](https://github.com/xteo/niuu/commit/b5f50ec90b592e58adcf36019eb717bcefff526c) | 2026-09-15 | Candidate | fix(skuld): preserve identity on metadata-limited previews |
| [b99449cc5](https://github.com/xteo/niuu/commit/b99449cc51a7fb381fc7fc71fe43f388a3f43580) | 2026-09-15 | Candidate | fix(niuu): drain socket pumps on connection cancellation |
| [3b4ff1976](https://github.com/xteo/niuu/commit/3b4ff1976cc29adc7e4dbb539785db20cddb3cf0) | 2026-09-15 | Candidate | docs(forge): record bounded-history candidate validation |
| [6512419c7](https://github.com/xteo/niuu/commit/6512419c730a14de117b438028beede00486e8e4) | 2026-09-16 | Candidate | docs(forge): prepare validated development integration review |
