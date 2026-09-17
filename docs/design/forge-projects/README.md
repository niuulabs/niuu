> **Implementation update (2026-09-11):** [IMPLEMENTATION.md](IMPLEMENTATION.md) is the current contract. A project is a Git meta-repository with multiple coordinator sessions, coordinated through a Forge CLI skill. Ting is not required. The original design below is retained as a design snapshot.

# Forge Projects — vision and recommended direction

**Design discussion · 11 September 2026 · Phase 1 of 3**

A project is a lasting stream of work, with one coordinating conversation, shared knowledge, and a family of Forge sessions. You describe outcomes to its coordinator; it organizes the work across repositories, hosts, and coding harnesses, keeps track of what happened, and brings back results you can inspect in Lexi iOS.

The first example is **Lexi**: Niuu/Forge, the Lexi backend, Lexi iOS, and potentially the WhatsApp bridge belong to one mission even though they have different repositories and deployment environments. Lexi Physics/Lia and work projects get their own missions, context, and allowed environments.

This branch contains a proposal, research, and an acceptance plan. It changes no runtime, database schema, running session, app, or deployment. Implementation follows the design discussion.

## The experience we are designing

Open **Project**, between Voice and Code. Choose Lexi. You return to its familiar Forge conversation with the coordinator, plus a compact view of current work and anything needing your attention. Say, “Improve startup, review the change, and prepare the iOS build.” The coordinator reads the project brief, chooses a workflow, creates the relevant child sessions, and follows their results.

A backend worker can run on Thor or Spark. iOS validation goes to a capable Mac. Each worker receives the objective, acceptance criteria, repository instructions, relevant decisions, and a specific revision of project context. It returns evidence and a handoff. Its complete conversation remains available through the existing Code experience.

You can open and steer a child directly. Returning to the project restores the coordinating conversation. Code gains **Group by Project**, alongside its current repository, host, and status choices. Projects stay visible when their coordinator is idle or stopped; archiving is an explicit organizational action.

## Recommendation

Build a thin project workflow over Forge, with three clear responsibilities:

1. **Forge runs and remembers sessions.** Reuse lifecycle, message delivery, native Claude/Codex transports, PostgreSQL event history, recovery, and mesh routing. Add public session labels, typed membership and parent references, and a small durable session-group record.
2. **The project coordinator manages intent and work.** It is an ordinary Forge session using a project role and project workspace. Start with Codex/Astra or Claude Code/Fable, selected through the installed host's model and effort capabilities. It delegates substantial implementation and verification so its conversation stays focused on decisions and direction.
3. **A deterministic workflow component remembers unfinished actions.** Reuse Ting's workflow/run concepts where they fit. Persist dispatch intentions, child references, result receipts, and pending coordinator notifications. An LLM turn ending or an iPhone disconnecting must not stop completion tracking.

The small session-group record gives the project a stable identity and points to its current coordinator. The UI still presents one project conversation. Replacing a broken coordinator preserves the project, its children, and its knowledge. A display tag alone cannot provide that continuity.

## Why we need a few backend changes

The reviewed integration already persists internal `workload_config` and supports prompt injection and host selection. We can therefore rehearse the workflow today with a project folder and ordinary sessions.

However, coding sessions do **not** currently expose a general project-label or parent relationship contract. Internal workload configuration is intentionally private and can contain authentication references. Chronicle tags and host tags describe other objects. Exposing the entire configuration or relying on session-name prefixes would create a fragile interface.

The proposed generic additions are **session groups, membership/lineage, and durable dispatch/result bookkeeping**. Project-specific language, onboarding, prompt templates, and presentation belong above those primitives. We should not create a second transcript store or a second agent runtime. The [architecture proposal](architecture.md) compares the configuration-only experiment with the supported product.

## Memory that survives the conversation

Each project has a normal Git repository containing its brief, repository and environment map, workflows, decisions, current checkpoint, and curated wiki. This is a coordination repository; application repositories remain separate.

PostgreSQL owns operational facts: membership, current coordinator, dispatch state, acknowledged messages, and replay events. Git owns versioned human-readable knowledge. GBrain indexes curated project knowledge and timelines, with project-scoped access. It can be rebuilt from authoritative documents; it is not the only copy of the plan.

Commit and back up knowledge continuously at meaningful boundaries. Record decisions when they happen, rather than waiting for compaction. Resume injects a bounded checkpoint and reconciles it with actual session state. Pre-compaction hooks provide additional protection where a harness supports them.

## The three-phase process

| Phase | Work | Exit condition |
| --- | --- | --- |
| **1. Align the vision — this proposal** | Capture the experience, research alternatives, identify existing components and the smallest reliable additions. | Agree on project identity, coordinator behavior, memory ownership, and the iOS navigation. |
| **2. Specify and challenge the contracts** | Define payloads, lifecycle and recovery rules, workflow templates, host routing, and simulator journeys. Rehearse one small cross-host workflow before broad implementation. | The design explains duplicate launches, a lost coordinator, an offline host, conflicting writes, and replay without depending on the model remembering. |
| **3. Implement and prove one complete pilot** | Deliver a Lexi project with one coordinator, a Linux child and a Mac child, shared context, result return, and iOS navigation. Inject failures, then expand the workflow catalog. | Restart/recovery and simulator acceptance pass; results and evidence are inspectable; no silent duplication or lost work. |

The initial pilot uses **Codex and Claude Code through the supported tmux path**, with capability-based effort selection. Project voice interaction follows once the underlying identity, dispatch, and history are reliable.

## Decisions for the next discussion

My recommended defaults are:

- One stable project, one current coordinator, replaceable without losing history.
- Thor as the initial owner for the Lexi pilot, with explicit host choices and a Mac for simulator work. Ownership is configurable per project; actual deployment readiness must be checked.
- A dedicated private project coordination repository; one service manages its canonical writes and Git synchronization.
- The coordinator can plan, research briefly, and maintain the brief; implementation and lengthy testing go to children.
- Workflow policy records what may be built, merged, and deployed. Existing user authorization carries through the workflow; tagging a session does not grant extra permissions.
- Five primary iPhone tabs: Channels, Voice, Project, Code, Settings. Loops is retired and is not part of this design, as confirmed during the discussion.

## Read further

- [Vision, user journeys, memory, and coordinator contract](vision.md)
- [Architecture, existing capabilities, API changes, and recovery](architecture.md)
- [Research and source-code evidence](research.md)
- [Pilot, failure scenarios, and acceptance criteria](validation.md)

The main external reference is Cursor's [Projects announcement](https://cursor.com/blog/projects), supported by its [launch changelog](https://cursor.com/changelog/projects). The architectural choices here are our proposal, grounded in the inspected Forge and Lexi code rather than an assumption that another product's behavior already exists in our stack.
