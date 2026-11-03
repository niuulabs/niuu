# Codex collaboration and long-running work

## Findings

Codex supports three complementary patterns: a long-lived native thread with
delegated children, independent runtime sessions supervised through an external
control surface, and durable repository documents that let work resume after
context loss. They solve different problems. A better Skuld frontend should make
the distinctions visible rather than label all of them “agents” or “resume.”

The public Codex 0.154.0 control logic also explains a material part of Ultra:
in the V2 multi-agent path, Ultra effort selects proactive delegation policy unless
configuration/catalog policy overrides it. This is not a separate agent runtime
or a fixed instruction to launch a specific number of children.

This assessment combines current OpenAI documentation, the earlier owned native
read-only audit, public versioned Codex code, and one practitioner-owned tmux
orchestrator implementation. It is not a popularity survey or a performance
benchmark. No provider experiments, configurations, external sessions or services
were changed.

## 1. Long-running single sessions: native state versus written plans

### Native goal and bounded continuation

OpenAI's Goals cookbook describes a persisted, thread-scoped objective with
lifecycle and budget accounting. An active goal may continue at idle boundaries,
not while another turn, queued user input or other pending work owns the thread.
Interruptions pause the goal; plan-only work does not trigger continuation, and a
continuation without tool activity suppresses another automatic continuation.
The research example explicitly separates reproduced mechanics, approximations
and claims blocked by missing evidence. This is an evidence-based completion
pattern, not a promise of uninterrupted autonomy through arbitrary host failure.
[OpenAI Goals cookbook](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)

The effective local App Server schema/read probe already established goal and
queue resources exist. That is stronger than assuming a plan list drives work,
but weaker than proving crash-safe queue dispatch or goal recovery in every
adapter. The research session itself was not placed in Goal mode merely to test
that feature.

### Plan mode is for resolving the approach

Current official guidance recommends starting with `/plan` when the outcome is
unclear, then turning the resulting constraints and verification criteria into a
goal. Related work remains in the same chat; independently writing sessions need
separate worktrees. A goal does not grant additional permissions.
[OpenAI long-running work guide](https://learn.chatgpt.com/docs/long-running-work)

Delegation remains useful during planning for bounded exploration and independent
review. Native progress updates can feed the UI, but their completion flags are
model-maintained progress—not an external evidence acceptance decision. The
frontend should show the current mode, proposed plan and execution checklist as
separate objects.

### Repository plans support re-entry

OpenAI's PLANS.md cookbook reports more than seven hours of work from one prompt
using a similar planning document. It explicitly describes “ExecPlan” as an
arbitrary prompting convention, not a native Codex protocol. Its useful pattern is
a self-contained, versioned document with progress, decisions, discoveries,
milestones, validation and remaining work, kept current at stopping points.
The duration is an author-reported example, not a reliability benchmark rerun here.
[OpenAI PLANS.md cookbook](https://developers.openai.com/cookbook/articles/codex_exec_plans)

A durable document helps a replacement agent understand what to do. It neither
keeps a process alive nor recreates a lost child registry by itself. Skuld should
link such documents alongside native goals/plans without presenting the document
as an executable scheduler. Historical model recommendations in that cookbook
are not evidence about this runner's effective Astra configuration.

## 2. Native children and returning agents

### Actual owned-session evidence

This runner has used **two native children**, not independent Forge sessions:
Laplace for protocol/Claude/workflow research and Gibbs for adapter/native-mechanism
research. They finished their earlier assignments, then received bounded
`followup_task` assignments on the same identities. The parent inspected returned
findings and independently verified critical source claims before incorporating
them. The [evidence record](evidence/skuld-cross-harness-20260914/README.md)
preserves their native IDs and objectives.

The earlier native read probe showed both child histories accessible while idle,
yet both had `canAcceptDirectInput=false`. Retasking through Codex's native
collaboration tools worked; that does not mean a frontend can send arbitrary
`turn/start` requests directly into those same children. Parent-mediated and
direct-client controls must be labeled differently.

This demonstrates reuse after an ordinary completed turn in the same session
tree. It does **not** demonstrate recovery after killing/restarting the parent
runtime; no such test was authorized or run.

### The native registry and messages

Public Codex 0.154.0 code scopes `AgentControl` to a root thread/session tree and
shares it with descendants. The registry is not the set of all threads in the
process or all Forge sessions. Native inter-agent communication therefore exists
without a shared tmux session or a NIUU collaboration room.
[Root-tree control scope](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/agent/control.rs#L116-L120)

At the effective tool surface, a message and a follow-up task differ: messaging
does not necessarily wake an idle recipient; follow-up work can. Listing,
waiting and interruption are native operations, not conventions derived by
scraping the parent transcript. The frontend should expose the sender, recipient,
delivery disposition and resulting attempt separately, so a finished child can
receive later work without appearing to be a newly created agent.

Public V2 dispatch resolves the target, requires membership in the known tree,
reloads an evicted known child if needed, and retains author/recipient identity.
Follow-up activation cannot target the root. The reload path preserves the stored
thread ID and model context rather than inventing a replacement. Sibling agent
paths are addressable within the same root registry. Native wait reports mailbox
activity, user input or timeout; it is not an evidence-review barrier. An interrupt
tool returning the previous status is likewise not proof the worker has stopped.
[Shared message dispatch](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/tools/handlers/multi_agents_v2/message_tool.rs#L52-L142),
[known-child reload](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/agent/control/spawn.rs#L314-L400),
[native wait](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/tools/handlers/multi_agents_v2/wait.rs#L53-L116),
[interrupt request](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/tools/handlers/multi_agents_v2/interrupt_agent.rs#L32-L103)

Cross-session tasks, independent process workers and native subagents must not be
joined merely because they run Codex. Re-entry starts by resolving the exact
namespace and ownership, then choosing a supported read/resume/message operation.
An agent visible in a registry is not blanket authorization to steer it.

### Cross-task tools are another native client surface

OpenAI's implementation PRs describe TUI-provided tools for listing, reading,
waiting on, creating, forking and messaging other Codex tasks, backed by existing
App Server thread operations. They also describe task mentions that resolve to
bounded thread references. This is a permissioned client/tool integration, not
the nested collaboration registry and not a shared checklist-task database.
These tools were not available on this audit's tool surface and were not exercised.
The PR descriptions were checked; all underlying handler bodies were not audited.
[Cross-task tools PR](https://github.com/openai/codex/pull/40308),
[task mentions PR](https://github.com/openai/codex/pull/40315)

Thus the CLI has another relevant capability beyond `/agent`. For Skuld, advertise
native cross-task access separately from native children and from Forge project
workers, with an explicit authorization scope. Do not infer access to every
conversation because one runtime thread is connected.

## 3. A Codex supervisor over tmux workers: concrete external implementation

The practitioner-owned **NTM** repository provides a relevant implemented example.
Source was inspected at commit `74ceb3236416f503797344a5a8a8d94ee061f584`, dated
September 14, 2026. Its controller can run Codex and supervise existing tmux worker
panes through machine-readable inventory/activity/output and an attention wait.
That relationship is established by the external controller and its worker
inventory—not by Codex's native child-thread registry.

The controller records the exact pane, provider and workers. It recommends
structured observation and blocking attention waits instead of manipulating the
human's terminal layout. Its implementation only reuses an appropriate verified
shell slot; it does not assume pane number one is available. The repository's
tests cover rejecting occupied or misleadingly labeled panes. These are useful
non-interference patterns; the source/tests were inspected, not run here.
[NTM controller](https://github.com/Dicklesworthstone/ntm/blob/74ceb3236416f503797344a5a8a8d94ee061f584/internal/cli/controller.go),
[controller tests](https://github.com/Dicklesworthstone/ntm/blob/74ceb3236416f503797344a5a8a8d94ee061f584/internal/cli/controller_test.go)

NTM documents worker worktrees and reservations: isolation prevents shared-file
edits from colliding, while reservations express ownership. Its pipeline resume
retains completed outputs and retries incomplete work, so operation idempotence
matters. These are external orchestration contracts, not capabilities conferred
by the model's plan list.
[NTM README](https://github.com/Dicklesworthstone/ntm/blob/74ceb3236416f503797344a5a8a8d94ee061f584/README.md)

One inspected restore path can reconstruct panes and launch plain `codex` when
the recorded command is missing. Consequently, restored terminal layout and
injected context cannot be advertised as proven native-thread resumption.
Other restore paths may differ; this is a bounded source finding.
[NTM restore command selection](https://github.com/Dicklesworthstone/ntm/blob/74ceb3236416f503797344a5a8a8d94ee061f584/internal/checkpoint/restore.go#L953-L967)

**Synthesis for Lexi:** borrow exact identity, structured observation, bounded
attention waiting and non-interference. Do not adopt another scheduler just to
obtain those properties. Ordinary Forge sessions plus Skuld native controls and
the existing project handoff store can retain the same boundaries.

## 4. Ultra: what is actually established

### Correction to the earlier capability interpretation

The installed 0.154.0 schema retains the `MultiAgentMode` type with explicit,
proactive and custom variants. However, the **turn-start field description marks
`multiAgentMode` deprecated and ignored** and directs clients to `effort:"ultra"`
for proactive multi-agent behavior. Treating the retained enum as an independently
effective setting was incorrect; the earlier report is corrected.
[Extracted effective mode contract](evidence/skuld-cross-harness-20260914/codex-mode-contract.json)

### Public runtime selection logic

The public `rust-v0.154.0` implementation selects effective delegation policy in
`effective_multi_agent_mode`:

1. The selector applies to the V2 multi-agent path.
2. A configured or model-catalog policy hint takes precedence.
3. Otherwise Ultra effort selects the model catalog's proactive policy, or the
   built-in proactive mode.
4. Other effort levels select the catalog's explicit-request policy, or the
   built-in explicit-request mode.
5. Session-source eligibility is checked separately.

This is public control logic, not a dump of this session's hidden instructions.
The effective binary's schema and the same-version public source agree on the
Ultra direction, but no live setting change or Ultra provider run was performed.
Matching version strings do not prove the installed binary is byte-identical to
the public release build or reveal the effective model-catalog override.
[Codex policy selector](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/session/multi_agents.rs#L155-L197)

### What it does—and does not—mean

Official guidance describes hosted Ultra as maximum reasoning with proactive
delegation, while general local-client guidance emphasizes explicit requests or
project/skill instructions. That broad local guidance is incomplete as a precise
description of the versioned selector above. Use versioned protocol/source plus
effective model capabilities, not a UI label or a generic documentation sentence,
to decide which controls are actually available.
[Official subagent guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents)

Ultra changes the delegation policy and available reasoning budget; the resulting
work still uses native agent threads and collaboration controls. It does not prove
that every prompt must launch agents, that a particular child count is forced,
or that peers continuously message each other. Native messaging is available, but
whether a particular run used it requires its public tool/event evidence. It is
not the A2A standard merely because one agent communicates with another.

The hosted product's complete scheduling, account policy, private prompt content
and performance tradeoffs are not established by this public-source audit. No
claim is made about unseen internal orchestration or a fixed optimum fleet size.

**Frontend implication:** show Plan/Default, effective reasoning effort, permitted
delegation behavior and child limits separately. Do not offer a functioning
`multiAgentMode` control on 0.154.0 just because its deprecated schema type remains.
Also do not silently convert a user's higher-effort selection into an unbounded
promise of parallelism; show actual children, actual activity and measured usage.

## 5. Recommended workflow refinements

These are recommendations inferred from the evidence, not newly implemented
behavior or benchmark conclusions.

| Situation | Prefer | Why / integration barrier |
|---|---|---|
| One coherent objective with bounded side investigations | Native children in the same Codex tree | Parent retains requirements; children return concise evidence; integrate only after needed results arrive |
| Multi-hour objective with uncertain next steps | Native goal where enabled, plus durable work notes | Objective survives turns; notes support re-entry; neither excuses evidence-free completion |
| Independent implementations or another harness/host | Ordinary Forge worker sessions with isolated worktrees | Explicit ownership/process identity; avoid pretending these are native children |
| Follow-up on a completed child assignment | Reuse the known child through supported native follow-up | Preserve identity/context rather than relaunching redundant workers |
| New unrelated user request during current work | Explicit pending submission with named delivery boundary | Steer versus after-turn versus after-objective are different intents |
| Reconnect or replacement coordinator | Read snapshot/history and durable receipt/plan before control | Distinguish same-thread resume, process restoration and new work from a handoff |
| Apparent stall | Inspect last activity, wait reason, process/native state and tool evidence | Quiet output or tool text is not proof of crash; no automatic adoption/restart |

Keep parallelism bounded by independent work and available resources, not by how
many agents can be launched. Retain a single owner for conflicting edits, explicit
barriers for integration, and a checked result/evidence requirement. Prefer native
event waits over repeated broad transcript reads. These are coordination practices
over ordinary sessions, not requirements for a new Ting/workflow engine.

For a future owned acceptance experiment, measure elapsed time, actual concurrent
children, duplicated work, parent-context growth, failed/uncertain controls, and
result quality against the same task and baseline. Distinguish exclusive from
transitive token usage to avoid double-counting children. No benchmark or
unattended scheduling experiment was run as part of this review.

## Deliverable boundary and next step

The [cross-harness API proposal](skuld-agent-capabilities-api-20260914.md) includes
the common language, visibility/control routes, capability reporting and acceptance
matrix. The first implementation decision should be a narrow **read-only agent,
plan/task and capability snapshot contract**, followed by qualified controls—not
a fleet scheduler or a UI that assumes parity the adapters do not have.

Review the API vocabulary and truthfulness requirements with runtime/frontend
owners, then authorize isolated adapter work and owned live acceptance cases.
No runtime or UI implementation, configuration changes, installs or deployments
are included in these research deliverables.
