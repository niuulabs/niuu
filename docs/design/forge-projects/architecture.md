> **Implementation update (2026-09-11):** [IMPLEMENTATION.md](IMPLEMENTATION.md) is the current contract. A project is a Git meta-repository with multiple coordinator sessions, coordinated through a Forge CLI skill. Ting is not required. The original design below is retained as a design snapshot.

# Architecture proposal: projects over ordinary Forge sessions

**Design candidate, not an implemented API.** All new field names, endpoints, capability names, and payloads below are provisional contracts for phase 2.

The reviewed backend baseline is Niuu `dev-integration` at `f44f62d5`. The initial working directory's `skuld-agent-tokens` branch is a different line at `596cacb5`; implementation should start from the reconciled integration, not accidentally reintroduce that older branch. The iOS baseline is local `main` at `791d70ba`, containing Live build 2253. [Research and evidence](research.md) lists the inspected sources.

## Existing foundation and actual gaps

| Concern | Existing code | Proposed work |
| --- | --- | --- |
| Launch and lifecycle | Forge create/start/resume/stop/archive/import APIs; native conversation IDs retained. | Reuse them; attach group and lineage atomically at creation/adoption. |
| Prompt and configuration | `system_prompt`, `initial_prompt`, launch specs, persisted internal `workload_config`, contributors. | Assemble versioned project context; verify injection and resume behavior in both selected harnesses. |
| Discovery and routing | Niuu aggregate uses `instance_id` or `target_tags`; iOS fans out through its instance pool. | Add membership filters; preserve canonical host-qualified identity; advertise feature support. |
| Relationships | No general coding-session labels/group/parent contract found. Chronicle tags and host tags serve other purposes. | Add public typed membership and parent reference, plus validated labels. |
| Replay | PostgreSQL session events, ordered cursors, recovery/import, recent-history negotiation. | Preserve them; link child results to their evidence and exact session. |
| Message delivery | Caller `request_id`; distinct delivered/pending/failure responses. | Reuse for notifications; add durable workflow consumption state and launch idempotency. |
| Staged work | Ting workflows, sagas, runs, campaigns, and pinned workflow snapshots. | Prove a native Forge adapter; reuse these instead of inventing another workflow language. |
| Git operations | Workspace preparation, branch and PR/merge helpers. | Project-aware worktree ownership, integration serialization, and multi-repository release evidence. |
| Knowledge | Generic session MCP contributor supports Mimir resources; Lexi service exposes GBrain tools. | Add project scope, snapshot assembly, canonical write ownership, and export/rebuild procedures. |
| iOS | Forge models preserve unknown fields; pool uses composite session keys; shared list organization. | Typed project/group models, Project tab, project filter/grouping, shared session-store lifetime. |

Existing capabilities are source findings, not a claim that each host is deployed with them or configured identically. Phase 2 must check the actual host/version/capability matrix.

## How little can we add?

| Approach | What it proves | Where it fails |
| --- | --- | --- |
| **Configuration-only rehearsal** | A coordinator can use a project directory, existing launch APIs, task prompts, and a file-based child index. | No supported public relationship query, weak concurrent updates, uncertain launch retry, and no durable automatic result return. Internal workload config must remain private. |
| **Session labels only** | Basic UI discovery and grouping become possible. | Replacing the coordinator, preventing conflicting ownership, validating parents, and recovering incomplete dispatch still need structured state. |
| **Small generic group plus session relationships — recommended** | Stable identity, one current coordinator, validated membership, a clear owner, and indexed discovery. | Requires a small schema/API addition and a workflow reliability adapter. |
| **Separate project runtime and transcript backend** | Could encode everything in a new subsystem. | Duplicates Forge lifecycle, replay, harness integration, and recovery. No demonstrated need. |

Use the configuration-only form for a short rehearsal. Ship the third approach. Tags remain useful discovery metadata, but they are not the authorization, referential integrity, or work ledger.

## Data model

**Session reference:** `{ instance_id, session_id }`, never a session name. Map device-local instance aliases to stable registry identities at the boundary. The existing iOS `<instanceId>:<sessionId>` key is the starting point, but registry/device identity equivalence must be verified. DNS endpoints can change without changing ownership. Two hosts with the same local session ID must never collide.

**Session group:** a small durable object hosted by one Forge owner, used by the Project UI when `kind = project`.

| Field | Purpose |
| --- | --- |
| `id` | Stable logical group/project UUID. |
| `owner_instance_id` | Authoritative Forge instance for group mutations and recovery. |
| `owner_id`, `tenant_id` | Existing access-control identity. |
| `kind`, `name`, `labels` | Product interpretation and presentation. |
| `status` | `draft`, `active`, `paused`, or `archived`; independent of a session process. |
| `coordinator_ref` | Current Forge session reference; nullable during setup/recovery. |
| `coordinator_generation`, `revision` | Fence obsolete coordinators and reject conflicting updates. |
| `context_ref` | Coordination repository reference plus last accepted context commit. |
| timestamps | Creation, update, archive, and last synchronization observations. |

**Session membership:** nullable `group_id`, `group_owner_instance_id`, `parent_session_ref`, `role`, and public `labels`. A role might be `coordinator`, `worker`, `reviewer`, or `validator`. Attach task/attempt/context identifiers through a validated public execution-context projection. Keep private authentication/runtime settings in their existing private configuration.

The current coordinator session has a discoverable `project` label and `role=coordinator`. The Project list deduplicates by stable group ID and follows its current coordinator pointer. A replaced coordinator can retain its historical role and label without producing another project row.

**Work and attempts:** a user-facing objective can have multiple execution attempts and sessions. The workflow layer records work ID, task brief revision, workflow revision, dependencies, selected host/harness/model/effort, dispatch ID, attempt, session reference, result/evidence, and pending actions. Reuse Ting's work/run representation where it fits; do not overload session lifecycle to mean task success.

**Parent rules:** one primary group per session in the first version; parent must be authorized and belong to the same group. Record the actual spawning session, including an old coordinator after replacement. Group membership provides project filtering; walking the parent tree is unnecessary for every list request. Reject self-parenting and cycles. Cross-project references are explicit links in documents initially, not implicit parenting or permission inheritance.

Future nested projects can introduce an optional parent group. The first release need not implement arbitrary graphs, multiple primary memberships, or recursive deletion.

## Minimal API surface

Continue to use `/api/v1/forge`. The proposed additional resource is `session-groups`; its `kind=project` view is the project-level endpoint the UI and coordinator need. A separately maintained `/projects` implementation would duplicate it. A convenience alias can be added later if a client requires one.

| Operation | Proposed surface |
| --- | --- |
| Discover/create groups | `GET/POST /session-groups`, with `kind=project` and archive filtering. |
| Read/update a group | `GET/PATCH /session-groups/{id}`, with a revision precondition for writes. |
| Discover sessions | Extend `GET /sessions` with `group_id`, `role`, labels, and a parent reference filter. Preserve current unfiltered behavior. |
| Create/adopt membership | Extend existing session create/update with the validated relationship fields. Require an explicit adopt operation in the UI. |
| Launch a worker | Existing session create plus a caller-generated dispatch/idempotency identity and context reference. |
| Steer/notify | Existing `/sessions/{id}/messages` with a stable `request_id`. |
| Replay/status | Existing session conversation, events, SSE and WebSocket endpoints. |

Group writes are routed to their owner; a worker is created on its selected execution host. Host routing hints remain distinct from labels describing a session. Extending the aggregate router, inner Forge router, persistence, SSE projection, and ForgeKit together is required; adding a field only to the inner API would repeat previous forwarding gaps.

There is no cross-database foreign key or distributed transaction. Before dispatch, the group owner validates the parent and reserves the relationship in the dispatch intention. The execution host verifies that authorized intention, stores the group/parent references with the child, and returns a creation receipt. The owner maintains a recoverable index of child references from those intentions and receipts. When either response is lost, reconciliation queries the recorded target using the same dispatch identity. Unknown remote state remains pending; a parent reference is never accepted solely because an agent put it in a prompt.

Illustrative child creation envelope; the fields marked proposed must not be sent to an old server as though supported:

```json
{
  "name": "lexi-voice-startup-review",
  "instance_id": "spark",
  "definition": "<discovered Codex or Claude-tmux definition>",
  "model": "<resolved model ID>",
  "source": {"type": "local_mount", "local_path": "<prepared worktree on Spark>"},
  "initial_prompt": "<bounded assignment and context reference>",
  "group_id": "<project UUID; proposed>",
  "group_owner_instance_id": "thor",
  "parent_session_ref": {"instance_id": "thor", "session_id": "<coordinator UUID>"},
  "role": "reviewer",
  "labels": ["project:lexi", "work:voice-startup"],
  "dispatch_id": "<stable dispatch UUID; proposed>",
  "context_revision": "<coordination-repository commit; proposed>"
}
```

Effort uses the deployed harness's existing supported launch configuration, not an invented universal top-level field. Persist the resolved effort in the work record. Advertise group/relationship/idempotent-launch support through feature discovery. An unsupported host must fail project launch clearly; silently ignoring membership fields would create orphan work.

## Dispatch and result reliability

A small durable reconciler operates independently of model turns. It may be implemented through Ting's dispatcher and persistence, with a native Forge adapter. If that adapter cannot support the pilot yet, use a narrow implementation of the same port and data contract rather than a second planning engine.

1. The owner commits a dispatch intention with project, work, attempt, chosen target, parent, and context revision.
2. The target atomically claims `dispatch_id` within the caller's tenant, persists the session and membership before starting its runtime, and returns the same session for an identical retry. A conflicting payload returns a conflict.
3. The owner records the returned session reference. If the response was lost, it queries/retries that same target and dispatch identity. It must not immediately try another host: the first worker might already be running.
4. The reconciler observes child state and persisted results. Cursor checkpoints survive its restart. SSE is an efficient wake-up signal; replay/polling repairs gaps.
5. A child result is accepted only with its work/attempt identity, producing session, evidence references, and outcome. The owner durably records it and queues a coordinator notification in one local transaction.
6. The notification uses the existing message API and a stable request ID. `pending` is not delivered; transport delivery is not model processing; processed is not verified acceptance.
7. The coordinator inspects the result, records a disposition, and advances the workflow. A deterministic state transition consumes the result once, even if its notification appears again after reconnect.

Use at-least-once delivery with deduplicated state transitions. Do not claim exactly-once model execution or deployment effects. An ambiguous provider send or publish attempt requires reconciliation against actual state before repetition. A new coordinator gets pending work routed using its new generation while the underlying result identity stays unchanged.

Observe a compact result envelope, not every token from every child. The child transcript remains the complete evidence stream in its owning Forge database. Persist event links/cursors and provenance in the work ledger; use the existing event infrastructure for control/audit events. Avoid a duplicate project transcript database.

The coordinator's durable notification queue supports idle wake-up. If it is busy, notifications queue/coalesce at defined turn boundaries; urgent user steering keeps its existing semantics. Coalescing must retain all underlying result IDs. Unknown or expired credentials produce actionable blocked state instead of repeated launch storms.

## Host selection and workspaces

Project configuration maps logical repositories to host-specific clones, baseline branches, setup commands, tests, and allowed deployment destinations. Host selection uses **hard requirements first**, then project preference: platform/toolchain, repository/data access, authorized credentials, supported harness/model/effort, capacity, and health. A Mac-required build must not silently become a Linux attempt.

Thor and Spark are candidates for Linux work; Mini is a candidate for simulator work. Their installed versions, capabilities, and availability must be measured before the pilot. A model display alias such as Astra or Fable resolves against the selected host; model names are not interchangeable with harness definitions.

Codex and Claude Code/tmux are the initial coordinator and worker targets. PI, Grok, Muse, and future harnesses can join through the same capability contract after context, delivery, and recovery checks pass; project identity must not encode a particular model vendor.

The worktree allocator creates a separate writable tree for an independently changing work item. Record repository identity, host, exact base commit, branch, path, and holder. A local mount merely points at a directory; it does not guarantee isolation. Same-task reviewer/validator access can use a pinned read-only checkout. Two writers never share a writable tree without explicit ownership and coordination.

Worktrees isolate development state, not operating-system permissions. Work and personal projects that require access separation need appropriately scoped service credentials and execution environments as well as separate memory queries. Membership labels alone do not establish that boundary.

Use one integration owner per repository/target branch. Recheck the remote base before landing; validate the rebased or merged candidate; make the final ref update conditional on the expected prior commit. Local locks alone cannot exclude an external Git writer. Do not force-push over unexpected work. Existing user-approved release/merge policies apply; a confidence score is not authorization.

Cross-repository delivery records a release manifest containing all repository commits, artifact checksums, tested compatibility, deployment order, target environments, and rollback points. There is no atomic Git merge across independent repositories. Use backwards-compatible changes and explicit rollout steps where required. Archive only after worktrees and artifacts are accounted for; cleanup must preserve uncommitted work.

## Context, knowledge, and operational authority

| Data | Authority | Recovery |
| --- | --- | --- |
| Mission, accepted decisions, curated wiki, workflow definitions | Versioned coordination repository. | Restore pinned commits from backup; retain local unpushed changes explicitly. |
| Group identity, membership, coordinator pointer and pending work | Owner PostgreSQL operational records. | Database backup plus reconciliation with execution hosts. |
| Full conversation/tool history | Each session's Forge/Skuld persistence. | Existing replay/import and database backup; native log availability checked separately. |
| GBrain index and derived timeline/search | Derived from attributed documents/events. | Reindex or restore a consistent snapshot through its owning service. |
| Build artifacts and large data | Existing artifact/data stores. | Independent retention/backup; references and hashes in the project repository. |

Git and PostgreSQL cannot be updated atomically. A context update therefore has an operation ID and states: proposed → Git committed → database reference accepted → remotely backed up. A crash between stages is reconciled using the operation and commit IDs. A worker sees only an accepted immutable revision. GitHub lag is visible and does not rewrite the current operational truth.

Canonical Git changes are serialized by the project owner service. Child reports and proposed wiki updates arrive under distinct IDs and are merged deliberately. GBrain receives project-scoped exports with source session, work ID, context revision, timestamp, and decision status. Current global search and unrestricted raw `brain_call` are not sufficient isolation for separate work/personal projects. Enforce scope in the tool/service boundary, not just in the prompt or a slug prefix.

The bootstrap packet contains a bounded project summary and links to its full documents. It is injected on fresh launch, recovery, and relevant context changes using tested harness mechanisms. Normal repository instruction discovery still applies. Local runtime source explicitly disables rewriting `CLAUDE.md`; preserve that behavior. Never assume Codex inherits a sibling coordination repository's `AGENTS.md` from an application worktree.

Checkpoint after meaningful actions, not only before compaction. Claude exposes relevant lifecycle hooks; Codex behavior must be verified against its installed version. A pre-compaction hook cannot cover a power failure. The broker/reconciler persists objective and dispatch/result transitions independently. On resume, provide the checkpoint and current ledger, then inspect the actual children before taking action.

## Recovery and lifecycle

The group survives a coordinator process stopping. First try native resume of the same Forge session. If replacement is needed, prepare a checkpoint, create a new session in a non-authoritative state, atomically change `coordinator_ref` and increment the generation, then allow it to dispatch. Record the replacement relation. Historical parent references remain unchanged.

All authoritative dispatch, integration, and context-write actions validate the current coordinator generation through the service. An old model process may still talk, but cannot act as a second project manager through those ports. Automatic owner failover is out of scope initially; losing the owner pauses new authoritative mutations until it is restored or explicitly migrated and fenced.

During an owner outage, already-running children may finish within their accepted assignments and persist results on their hosts. Owner/project membership is cached for display, marked stale. Do not create an alternative coordinator merely because an iPhone request timed out. On return, reconcile results before launching replacements.

| Action | Meaning |
| --- | --- |
| Stop coordinator | Stop its runtime; group and running children remain. |
| Pause project | Prevent new dispatch; show ongoing children and queue incoming results. |
| Stop all work | Explicit fan-out with per-session acknowledgments and visible unreachable targets. |
| Archive project | Preserve group and history; prevent new dispatch. Require ongoing work to be stopped or explicitly retained/acknowledged. |
| Delete a session | Preserve historical group/parent tombstone and retained evidence references according to policy. |
| Purge project | Separate retention-aware operation; never implied by archive or coordinator replacement. |

## iOS integration and compatibility

Add `Project` to `AppRouter` and the iPhone/iPad shell order. Add group models and typed relationship fields in ForgeKit while preserving its unknown-field passthrough. Extend the current organizer with project grouping and filters. Share `ForgeSessionStore` above both Project and Code rather than allocating one per tab; otherwise the same session can acquire duplicate live models and inconsistent drafts.

List group metadata and bounded status summaries first. Fetch only the selected session's recent replay. A project needs a timeline of meaningful events and references, not a concatenation of all child event streams. Show partial host failure explicitly and retain last-known rows with freshness markers; a successful response from one host is not evidence that all projects were listed.

Roll out server capabilities before enabling the iOS creation UI. Legacy sessions decode with no group and remain in Unassigned. Existing clients continue to list ordinary sessions. Parent/coordinator replacement, archive filtering, and direct links must work through both direct host connections and the aggregate facade. Browser rendering can continue using the current session view until project navigation is separately designed.

## Implementation boundaries

Forge/Volundr owns generic group and session contracts. Skuld owns native transport, context delivery, and transcript capture. Ting or a narrow workflow adapter owns work/run progress and durable coordination. The project role supplies planning behavior and the versioned workflow selection. Lexi iOS supplies the product experience.

Respect existing module boundaries: no Ting↔Volundr imports. Use ports, public APIs, and shared value types only where necessary. Any actual migration must follow both migration locations required by this repository. The [acceptance plan](validation.md) is the gate for claiming this design has become a dependable product.
