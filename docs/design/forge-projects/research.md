> **Implementation update (2026-09-11):** [IMPLEMENTATION.md](IMPLEMENTATION.md) is the current contract. A project is a Git meta-repository with multiple coordinator sessions, coordinated through a Forge CLI skill. Ting is not required. The original design below is retained as a design snapshot.

# Research and implementation evidence

**Reviewed 11 September 2026.** External statements below describe the fetched official documentation. The “application here” statements are design inferences, not claims that these products implement our proposed Forge contracts. No external product was installed or benchmarked for this review.

## Cursor Projects

Cursor's 10 September announcement describes a continuing coordinator conversation for a substantial body of work. The coordinator delegates implementation, retains shared file context, can combine cloud execution with local testing, and can react to subscriptions. These are product descriptions; this review does not use the article's productivity or agent-count claims as evidence for our own scale targets. [Announcement](https://cursor.com/blog/projects)

The accompanying changelog confirms the project navigation, a coordinator that plans and delegates, context files shared across agent machines, and recurring/event-triggered work. It describes a beta rollout. [Changelog](https://cursor.com/changelog/projects)

**Application here:** retain the ongoing coordinating conversation, shared knowledge, and execution away from the phone. Our implementation must additionally define cross-Forge identity, different harnesses, owner recovery, and existing iOS replay behavior. Start with a small real workflow; defer subscriptions until delivery and reconciliation are dependable.

## Conductor

Conductor's first-workspace guide describes a task/PR workspace with its own branch, working tree, and setup context. It distinguishes tracked Git files from ignored configuration and dependencies supplied by setup. The guide includes Claude Code and Codex among supported agents and describes reviewing, testing, merging, and archiving workspace work. [First workspace](https://www.conductor.build/docs/first-workspace)

Its parallel-agent guidance distinguishes independent workspaces from multiple agents sharing one workspace. Shared workspaces simplify access to the same branch but permit conflicting edits; independent changes benefit from separate branches and environments. [Parallel agents](https://www.conductor.build/docs/concepts/parallel-agents)

**Application here:** distinguish a long-lived project from an individual execution workspace. Assign isolated writable worktrees to independent changes and explicit ownership to shared work. A cross-repository project cannot be represented solely by one application's repository path.

## Claude Code

Claude's agent-teams documentation describes a lead, independent teammate contexts, messaging, and task coordination. It also documents important limitations: experimental status, in-process teammates not restored by resume, fixed leadership, and restrictions on nesting. A teammate receives its spawn prompt and project context, not the lead's entire conversation. These details are version-sensitive. [Agent teams](https://code.claude.com/docs/en/agent-teams)

Claude's memory documentation describes project-root `CLAUDE.md` being reloaded after compaction. That is a useful instruction mechanism, but it does not turn every prior conversation fact into durable project knowledge. [Memory](https://code.claude.com/docs/en/memory)

The hook reference includes session start, subagent events, and pre/post-compaction hooks. Such hooks can save or reload context at supported lifecycle boundaries. They do not promise a callback before a process or host crashes. [Hooks reference](https://code.claude.com/docs/en/hooks)

**Application here:** use native helpers inside a Forge worker when appropriate, but make independently recoverable or cross-host work a separate Forge session. Verify hooks in our actual tmux transport. Preserve explicit project checkpoints and context revisions outside any one Claude conversation.

## Codex

Official Codex documentation describes specialized subagents, thread controls, and configurable agent instructions/model/effort. In local clients, delegation follows an explicit request or applicable project/skill instructions. The document recommends bounded work and notes the added cost and conflict risk of concurrent writers. [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)

The instruction documentation explains how Codex discovers `AGENTS.md` through global and repository/directory scopes at startup, with nearer overrides and a configured size limit. A coordination repository in an unrelated directory does not automatically become part of an application worktree's instruction chain. [AGENTS.md discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

**Application here:** make delegation an explicit part of the accepted project role and inject a small task/context packet through a verified launch mechanism. Keep normal repository instructions intact. Resolve model/effort on the selected host. Native subagent tooling complements Forge's durable child-session graph; it does not replace our cross-host ownership contract.

The OpenAI documentation search resolved these topics to `learn.chatgpt.com`. An initial broader best-practices fetch returned 404; the specific instruction and subagent pages above were successfully fetched and read. No behavior is attributed to the failed fetch.

## Persistent workflow systems

LangGraph's persistence documentation distinguishes thread checkpoints from stores containing cross-thread knowledge, and warns that in-memory checkpoints disappear with the process. [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

**Application here:** keep operational checkpoints and project knowledge distinct. This is a useful architectural comparison, not a recommendation to add LangGraph: Niuu already contains Ting's durable workflow concepts. We should first prove that our existing components can carry the required dispatch and recovery contract.

## Source-code findings

The design branch is based on **Niuu `f44f62d5d309aa8f233744dd2a44f53cb04b2c8b`**, matching `origin/forge/dev-integration` when checked. These relative links resolve to the same inspected code on this branch.

| Source | Evidence and implication |
| --- | --- |
| [Session model](../../../src/volundr/domain/models.py) | Coding sessions retain lifecycle, native IDs and workload configuration. No general session-group/parent/label contract is present in this model. Other classes' tags/parents must not be confused with session fields. |
| [REST contracts](../../../src/volundr/adapters/inbound/rest.py) | Session create accepts definition, prompts, launch spec and workload config. Public responses intentionally omit raw workload config. List supports lifecycle/archive filtering; group filters are proposed additions. |
| [PostgreSQL session repository](../../../src/volundr/adapters/outbound/postgres.py), [workload migration](../../../migrations/000054_sessions_workload_config.up.sql) | Workload configuration is persisted as JSONB. It is not safe to expose wholesale: the model explicitly warns that it may include auth references. |
| [Aggregate facade](../../../src/niuu/adapters/inbound/rest_volundr.py) | Routes creation via instance ID or target tags, forwards calls and adds instance identity. Public project behavior must be implemented through this layer as well as the inner router. |
| [Session lifecycle service](../../../src/volundr/domain/services/session.py) | Restarts reuse persisted configuration. New group fields must survive the same lifecycle, without redefining existing status semantics. |
| [Session event repository](../../../src/volundr/adapters/outbound/pg_session_event_log.py) | Durable per-session event ingestion and sequence-based reads already exist. Extend provenance and references rather than duplicating transcript storage. |
| [Message delivery endpoint](../../../src/volundr/adapters/inbound/rest.py#L2766), [broker](../../../src/skuld/broker.py) | Existing request IDs and delivery states are valuable. They do not by themselves supply an idempotent cross-host create operation or a consumed-result work ledger. |
| [Native process adapter](../../../src/volundr/adapters/outbound/local_process.py) | `_write_claude_md` is deliberately a no-op because old injection clobbered repository guidance and leaked stale tasks. Keep project injection separate. A mounted path is not a worktree allocator. |
| [Session MCP contributor](../../../src/volundr/adapters/outbound/contributors/session_mcp.py) | Generic attached-resource contribution already exists, with Mimir as the current supported resource. This is an extension seam, not evidence that GBrain project scoping already works. |
| [Orchestrator guide](../../openclaw-session-orchestrator-guide.md) | Separates control APIs, lifecycle events, and live transcript transport. The proposed coordinator should keep that distinction. |
| [Workflow concepts](../../site/concepts/workflows-and-teams.md), [workflow snapshots](../../../src/ting/domain/workflow_snapshot.py) | Ting is already the layer for staged workflows. Snapshots retain workflow identity/version/graph and resource bindings. |
| [Saga repository](../../../src/ting/ports/saga_repository.py), [campaign repository](../../../src/ting/ports/workflow_campaign_repository.py), [system workflows](../../../src/ting/system_workflows.yaml) | Existing persistence and build/review/delivery workflow definitions should be evaluated before adding a new run engine. A cross-host native Forge adapter is not demonstrated by these interfaces alone. |
| [Git workflow service](../../../src/volundr/domain/services/git_workflow.py) | PR/merge helpers exist. Project-level multi-repository integration queues, worktree ownership, and release manifests remain proposed work. |
| [Module boundaries](../../../.claude/rules/module-boundaries.md), [migration rules](../../../.claude/rules/migrations.md) | Workflow integration must respect package boundaries. Any future schema change must be mirrored into the Helm migration configmap. |

### Lexi iOS

Read locally at **`791d70ba930a878d2cee6e0c0030ef53c3881134`**, which includes Live build 2253. At the check, those Live commits had not reached GitHub `main`. The linked files below were verified unchanged between the published `60c09151` and that local head, so the links use the available published revision.

| Source | Implication |
| --- | --- |
| [App router](https://github.com/xteo/lexi-ios/blob/60c0915168d59e12588837d78a4647946798699c/apps/chat/LexiChat/V2/AppRouter.swift), [tab view](https://github.com/xteo/lexi-ios/blob/60c0915168d59e12588837d78a4647946798699c/apps/chat/LexiChat/V2/AppTabView.swift) | Add Project between Voice and Code, with its own navigation path. **User clarification: Loops is dead and not needed; do not relocate or revive it.** |
| [Session organization](https://github.com/xteo/lexi-ios/blob/60c0915168d59e12588837d78a4647946798699c/apps/chat/LexiChat/V2/Model/ForgeSessionOrganization.swift) | Repository/status/host grouping and composite-key pins already exist. Add Project as a grouping and filter dimension. |
| [Forge session model](https://github.com/xteo/lexi-ios/blob/60c0915168d59e12588837d78a4647946798699c/packages/ForgeKit/Sources/ForgeKit/Models/FKSession.swift) | Unknown fields survive decode/re-encode. Add typed relationships without breaking older servers or losing passthrough fields. |
| [Instance pool](https://github.com/xteo/lexi-ios/blob/60c0915168d59e12588837d78a4647946798699c/packages/ForgeKit/Sources/ForgeKit/Mesh/ForgeInstancePool.swift) | Host fan-out, health/circuit handling, and composite-key deduplication are reusable. Group discovery still needs freshness and partial-failure behavior. |
| [Session store](https://github.com/xteo/lexi-ios/blob/60c0915168d59e12588837d78a4647946798699c/apps/chat/LexiChat/V2/Views/ForgeSessionStore.swift) | Live models are cached by host/session identity. Share that ownership across Project and Code. Some historical comments describe old persistence limitations; this proposal does not treat those comments as current backend behavior. |

### Lexi service and GBrain

Read locally in `lexi-frontend` at **`552b770c04084e5c546716b3dd34ea43d88f5cef`**:

- `services/lexi-agent-service/tools/forge/index.ts`: the current create tool takes a source path, model, prompt, and definition, and is marked non-idempotent. It does not expose the proposed project context, dispatch identity, or host-selection contract. Extend a shared tool adapter rather than assuming the present wrapper covers multi-host project launch.
- `services/lexi-agent-service/tools/brain/index.ts`: existing read/search/write/capture/timeline tools provide a useful knowledge interface. Search is global in this wrapper; project-scoped enforcement is proposed work.
- `services/lexi-agent-service/tools/brain/client.ts`: one long-lived stdio GBrain process and serialized requests protect the local single-writer store. The shared folder is a backup target, not the primary store. Do not duplicate that process per worker.

This review inspected code and documentation. It did not alter the brain, ingest private project data, interrogate unrelated conversation logs, or verify live host deployment parity. Those belong to the bounded pilot.

## Design conclusions

1. An ongoing coordinator and shared project context fit the requested experience and existing session stack.
2. Labels help discover sessions; a stable group and explicit relationships make them recoverable.
3. A model's memory, a native subagent hierarchy, a workflow ledger, and a full transcript are different layers.
4. A small deterministic dispatcher/reconciler is necessary for reliable unattended progress.
5. Existing Ting, Forge, Skuld, ForgeKit, and GBrain seams should be reused before introducing new infrastructure.
6. The first success criterion is one correct, recoverable cross-host workflow in iOS, not a large agent count.
