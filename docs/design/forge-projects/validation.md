> **Implementation update (2026-09-11):** [IMPLEMENTATION.md](IMPLEMENTATION.md) is the current contract. A project is a Git meta-repository with multiple coordinator sessions, coordinated through a Forge CLI skill. Ting is not required. The original design below is retained as a design snapshot.

# Pilot and acceptance plan

**Planning artifact only.** These scenarios have not been executed for a Projects implementation. This branch adds documents, not tests that claim the feature exists. Validation of this deliverable is limited to document consistency, references, and Git publication.

## Three phases and their review gates

### Phase 1 — vision and feasibility

Deliver the executive summary, experience, technical options, source evidence, and this acceptance plan. Discuss the proposed stable project identity, coordinator replacement, knowledge ownership, and UI. Incorporate corrections before implementation. The confirmed Loops decision is already reflected: it is not part of the new tab layout or scope.

### Phase 2 — contracts and a bounded rehearsal

Write the actual schema/API contract and state transitions; specify the context bundle and prompt templates; map the selected workflow onto existing Ting concepts. Inspect Thor, Spark, and Mini capabilities and authentication without changing active work. Rehearse with one disposable project workspace and ordinary sessions only when that phase is authorized.

Resolve these contracts before calling the implementation ready:

- Canonical instance identity across direct iOS connections and the Niuu registry.
- Ownership, revision checks, adoption, coordinator replacement, and tombstone rules.
- Idempotent create and lookup semantics, including payload conflicts and lost responses.
- Pending/delivered/consumed/verified result semantics and durable event cursors.
- Native context injection at fresh launch, resume, compaction, and harness/model change.
- Ting adapter support for native Forge sessions; location of the small durable reconciler if a gap remains.
- Project-scoped GBrain access and Git/PostgreSQL reconciliation.
- Worktree ownership, branch integration policy, capability discovery, and UI fallback for older hosts.

Challenge the design against the fault matrix below. Review can use independent agents when explicitly requested; no additional reviewer sessions have been launched by this design work.

### Phase 3 — implement one complete Lexi pilot

Implement in slices that preserve existing session behavior:

1. Group and relationship persistence/API, aggregate forwarding, capabilities, and backward-compatible decoding.
2. Coordinator context workspace and recoverable dispatch/result flow.
3. Project tab, filtered Code navigation, shared cache ownership, and recent-history replay.
4. One build-and-review workflow with isolated workspaces and host requirements.
5. Fault injection, recovery, and documented rollout.

Use Codex/Astra and supported Claude Code/tmux/Fable configurations discovered on the hosts. Preserve the user's preferred extra-high effort where supported; show the effective value and do not silently downgrade it. A host without a compatible credential or harness is unavailable for that assignment.

## First end-to-end story

Create a disposable **Lexi Projects Pilot** coordination repository with a brief, two repository bindings, and explicit Linux/Mac host preferences. Start one coordinator on the chosen owner. Ask it to inspect a small backend interface and validate a corresponding iOS fixture. The task should be bounded, reproducible, and avoid production deployment.

Dispatch a Linux worker and a Mac simulator worker with pinned repository/context revisions. Use a different supported harness for an independent review. Each returns a structured result with its session reference, commit or unchanged-source identifier, test evidence, limitations, and recommended disposition.

Open the project on iOS, inspect a child in Code, steer it, and return to the coordinator. Close the app while work is running. Restart the owner/reconciler at a controlled fault point, resume or replace the coordinator, and demonstrate that the same children and results remain connected. Finish with a project checkpoint and a remotely verified context backup.

No physical-phone testing is required. UI validation uses the iPhone simulator, with iPad navigation coverage where the shell changes. Actual release or deployment validation is a subsequent workflow with an explicitly chosen target.

## Required acceptance scenarios

| ID | Exercise | Expected evidence |
| --- | --- | --- |
| P01 | Create project with no application repository, then add two repositories. | One stable group, one current coordinator, versioned brief, correct bindings. |
| P02 | Adopt an existing Forge session and attach an existing child. | Original native ID and replay preserved; explicit membership; no bulk accidental adoption. |
| P03 | Launch children on two hosts, including IDs that collide locally. | Distinct canonical session references and correct routing from iOS. |
| P04 | Launch a nested child within one project. | Actual parent preserved; group filtering includes descendants without loading all transcripts. |
| P05 | Attempt self-parent, cycle, unauthorized parent, or different-project parent. | Clear rejection and no partial mutation. |
| P06 | Retry a child launch after target commits but its response is lost. | Exactly one session record for the dispatch; conflicting retry payload rejected. |
| P07 | Restart target after dispatch claim and before runtime starts. | Reconcile the same claimed session; no duplicate process allocation. |
| P08 | Lose target connectivity while launch status is unknown. | Pending/unknown is visible; no speculative launch on another host. |
| P09 | Complete a child while coordinator is idle and iOS is closed. | Durable result and notification; coordinator resumes through the defined mechanism. |
| P10 | Duplicate/reorder notifications and restart the reconciler. | One accepted workflow transition per result; replay cursor and pending notifications survive. |
| P11 | Force message API pending, rejection, and ambiguous timeout. | Distinct UI/work states; transport delivery never reported as accepted task completion. |
| P12 | Stop/resume coordinator; then replace it when native resume is unavailable. | Same project ID; checkpoint restored; new generation; old children remain linked. |
| P13 | Allow the old coordinator to issue a late dispatch after replacement. | Owner rejects stale generation; no second authoritative coordinator. |
| P14 | Take the owner offline while children continue. | Cached project marked stale; assigned children retain results; new mutations pause; recovery reconciles first. |
| P15 | Compact or restart Codex and Claude coordinator sessions. | Objective, accepted constraints, context revision and active work remain available; repository instructions intact. |
| P16 | Crash before a pre-compaction/stop hook can run. | Durable operational records recover the work; stale checkpoint does not cause duplicate dispatch. |
| P17 | Interrupt between Git commit, DB context acceptance, and GitHub backup. | Operation reconciled by identity; only accepted revisions dispatched; backup lag reported honestly. |
| P18 | Two children propose conflicting knowledge edits. | Proposals retained; canonical write serialized; no silent lost update or concurrent brain writer. |
| P19 | Search or write memory from the wrong project, including raw-tool bypass. | Scope enforced at service boundary; no cross-project data returned or altered. |
| P20 | Make GBrain or GitHub unavailable. | Required cached context still usable; pending export/backup visible and retried; no fabricated success. |
| P21 | Request Mac-only work on Linux; use expired credentials or unsupported effort. | Capability/credential failure visible before inappropriate execution; selected fallback requires valid policy. |
| P22 | Run competing writers and race a remote branch update. | Separate worktrees, enforced integration ownership, expected-ref check, retest changed candidate. |
| P23 | Validate a cross-repository candidate. | Evidence names every revision/artifact and deployment dependency; partial completion stays explicit. |
| P24 | Navigate Project → child Code session → Project, including direct links. | Correct project filter and breadcrumb; preserved draft/scroll; no duplicate live socket/model. |
| P25 | Reopen a project with very long coordinator and child history. | Recent coordinator replay loads first; older pages on demand; no eager fan-out of child logs. |
| P26 | Mix old and new hosts; fail one host during list refresh. | Unassigned legacy sessions still work; unsupported creation is explicit; stale project rows and partial failure shown. |
| P27 | Pause project, stop coordinator, stop all, archive, and restore. | Distinct effects; per-host stop acknowledgments; no implicit deletion or forgotten running child. |
| P28 | Delete/retire a parent session while retaining a project. | Tombstone/history references remain navigable; group identity does not disappear. |
| P29 | Restore backup to a replacement environment. | Knowledge and operational data both restored, instance mappings reconciled, old owner fenced before new dispatch. |
| P30 | Inspect fresh and cached replay containing prose, tools, failure and child links. | Interleaving, result attribution and evidence links are consistent; no false successful-work status. |

## Evidence format

Each run should retain a compact manifest: scenario ID; source commits; server/harness versions; model and effective effort; canonical instance/session/group/work/dispatch IDs; context and workflow revisions; initial fault point; expected and observed outcomes; artifact paths; and timestamps.

Store selected redacted event sequences and response states alongside assertions. Use synthetic project data for shared fixtures. Keep raw authenticated logs and actual conversation exports in private validation storage; reference them without copying credentials or unrelated private material into the design repository.

Turn representative successful and failed pilot traces into replay fixtures for ForgeKit and simulator journeys. Verify invariants against recorded IDs and events. Live LLM wording can vary, so assert task structure, delivery state, evidence, and final outcome rather than exact sentences or a predetermined number of tool calls.

## Performance and operational checks

Provisional pilot targets, to be measured rather than promised:

- Cached Project list is visible within 300 ms of tab selection on the chosen simulator/device class.
- Healthy mesh metadata refresh completes within 2 seconds at p95 in the pilot environment; failed hosts do not prevent cached results from appearing.
- Project navigation performs no full-history child fan-out. Start with a maximum 50-item page for work/session listings and inspect the actual response size.
- A child result is recorded and queued for notification within 2 seconds at p95 after the owner receives its durable terminal event. Provider turn latency and offline delay are measured separately.
- Cross-tab navigation maintains one live model/socket per selected session identity under the app's existing retention policy.
- Pilot duplicate-delivery and restart scenarios produce zero duplicate dispatch effects or lost acknowledged work records. This is an observed acceptance result, not a universal exactly-once guarantee.

Capture timings separately for UI/cache, host discovery, group/session fetch, context assembly, workspace preparation, harness startup, input delivery, work execution, result persistence, coordinator wake, integration, and backup. The logs must make it possible to distinguish our stack's delay from the model's delay.

## Completion and rollout

The pilot is complete when a user can direct work from Project, inspect each child and its replay in Code, recover from coordinator interruption, and verify the outcome and its evidence without reconstructing the workflow manually.

Roll out capabilities first, then enable the iOS Project entry for supported environments. Retain ungrouped Code sessions and old-client compatibility. Disable new dispatch or the Project UI if a regression is found while preserving membership, transcripts and worktree data. Expand beyond the pilot only after the failure scenarios pass and their limitations are recorded.
