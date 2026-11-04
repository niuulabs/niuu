# Skuld agent and planning research plan

## Objective and scope

Expose existing harness capabilities through one discoverable Skuld API for human
frontends and authorized agent clients. Define a common language without pretending
all harnesses have identical control, planning, persistence or collaboration semantics.
This is research and a proposed contract, not a shipped API or deployment assignment.

The gateway is **Skuld**. The confirmed requested harnesses are Claude/tmux, Codex,
Grok (registered as **xAI Grok Build**), Pi and Meta Muse Code. There is no separate
Build harness in scope. Include OpenCode as an explicitly supplementary comparison
because it is also registered; its Build/Plan roles are not separate harnesses.

## Task register

| ID | Work | Owner | State | Depends on | Completion evidence |
|---|---|---|---|---|---|
| R1 | Confirm harness identities and adapter boundaries | Parent | completed | — | Source registry and direct name confirmation |
| R2 | Claude/tmux subagents, teams, plans/tasks | Existing native child `app_server_protocol` | completed | — | Official docs plus tmux source mapping; key claims independently checked |
| R3 | Grok and Muse capabilities/protocols | Existing native child `niuu_projection` | completed | — | Pinned vendor sources plus adapter mapping; key claims independently checked |
| R4 | Pi, OpenCode and effective Codex baseline | Parent | completed | — | Primary sources and version-qualified evidence |
| R5 | Agent and planning comparison matrices | Parent | completed | R1–R4 | Native/projected/unknown distinctions in comparison report |
| R6 | Common vocabulary, proposed API and acceptance matrix | Parent | completed | R5 | First design draft, not approved implementation |
| R7 | Codex long sessions, supervisor/tmux patterns, Ultra | Parent with two bounded child follow-ups | completed | R6 | Public selector/message/reload sources, official long-session examples and practitioner source inspected |
| R8 | Validate citations/contracts, commit/push and hand off | Parent | completed | R6–R7 | Reports pushed at `d476938a`; matching saved receipt response; non-dry-run OpenClaw message ID |

This Markdown task register is a durable research checklist, not a background
scheduler. The parent actively executes and updates it. Runtime completion is
distinct from inspecting and accepting a child's findings.

## Execution boundaries

- Reuse the two already-created native children; maximum two concurrent research
  children and no fanout, extra Forge sessions, provider experiments or installs.
- Each child gets one bounded read-only track, about twelve minutes and 8–12 focused
  source retrievals before returning evidence or explicit uncertainty.
- Parent owns this worktree's documentation, synthesis and verification. Children
  do not write files. No concurrent product edits or merges with other owners.
- Read local contracts first. Prefer current official docs and first-party sources.
  Installed binary/schema evidence is qualified separately from documentation and
  from actual effective launcher/runtime observations.
- No other live conversations, service changes, auth/config mutations, device work
  or external worktree changes. No private reasoning/transcripts in deliverables.
- Phase two follows the comparison/API draft; it does not postpone the first-phase
  findings or grant permission to build a new scheduler.
- Send meaningful milestones through the existing OpenClaw route. Keep the shared
  hourly reporter; create no new schedule. Share documents as full host-path links.

## Recovery and ownership

Source worktree: `/home/thor/repos/worktrees/niuu-codex-subagents-20260914`.
Source base: `21bb0de6ec0ec467865382dd99640cf37b6cffdb`; prior audit `b67d95c6`.
Runner: `thor:0a713405-79b2-566c-a953-802e3a080c49`.
Supervisor: `thor:8f20102d-6da7-58aa-98c2-e0bce2deab97`.
Session remains open for direct iteration after the research deliverables.

## Outcome

Both research phases are complete as research/design, not implementation or product
acceptance. [Comparison and API proposal](skuld-agent-capabilities-api-20260914.md)
and [Codex workflow study](codex-collaboration-workflows-20260914.md) are available
as in-session full host-path links. Both existing children finished; key findings
were inspected. No orphan research, extra Forge sessions or fanout remain.

Final coordinator handoff receipt is saved privately before delivery under
`.local/audit/cross-harness-final-receipt.json`, with the matching response.
OpenClaw completion milestone was delivered with a returned message ID and
`dryRun:false`; no new hourly schedule. Delivery is not coordinator acceptance.

Pending user iteration: API vocabulary, capability honesty, read-only snapshot
contract, then separately authorized adapter/UI work and live qualification.
The Ultra field deprecation correction is reflected in both earlier reports.
