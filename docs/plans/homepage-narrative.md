# Homepage narrative

Status: implemented in this worktree as an uncommitted proposal, 2026-10-31.

The page states selling points in a line each. Detail goes to the docs; `docs-rewrite.md` lists where.

The first version answered "what can I run?". That made NIUU look like a tool for
launching coding agents. This version answers "where does this work live, and why
does it keep getting further?". The session stays as the familiar starting point,
and the page shows how work grows from it: session, workflow, team, resident.

## Structure

| # | Section | Heading | Purpose | Proof on the page |
|---|---|---|---|---|
| - | Hero | A place where intelligent systems do real work. | Unchanged promise and supporting copy. The buttons lead into the idea, not a command. | Brand field |
| 1 | The idea | Work doesn't start over. It evolves. | Sets the category in one screen. Continuity now means responsibility, decisions, knowledge and capability, not saved sessions. | Four things that carry forward |
| 2 | Where it starts | You start with a session. Then you want more. | Familiar ground: the chat people already know, then the path from it. Session, workflow, team, resident. | A session chat and a four-stop path |
| 3 | Why NIUU | Agents that work for you, not agents you have to drive. | The central argument. Steering every session does not scale, and open-ended access is not safe. Workflows hold both. | A shipped workflow, plus process, responsibility, tools and model, and reaching out |
| 4 | The environment | People and agents share a place to work. | Shows what a Workspace holds and that it is not only for code. | Workspaces screen |
| 5 | In practice | One responsibility, followed through. | Replaces the screenshot gallery. Six steps on one path: set, notice, bring in help, decide, keep, improve. | Six real screens in order |
| 6 | Visibility and trust | Capable on its own. Accountable to you. | Answers the objection the story raises. Telemetry, security and tenancy are named as platform features. | Telemetry timeline and three pillars |
| 7 | One environment | Six parts of one environment. | Keeps the architecture. Each card says what it makes possible. | Product cards |
| 8 | Direction | Toward an operating system for agents. | Holds the ambition, and marks it as direction. | Link to the direction page |
| 9 | Built on what you have | We extend the best tools. We don't rebuild them. | NIUU combines existing harnesses, models, trackers and infrastructure. Moved from the middle of the page to near the end. | Harnesses, models, ticketing, identity, observability, targets |
| 10 | Get started | Try it on your own machine. | Commands stay, at the bottom, for those ready for them. | Commands and quick start link |
| 11 | Closing | Give intelligent systems a place to do real work. | Ends on identity, not on a command. | Docs map |

## Why this is different from agent orchestration

An orchestrator starts with a task and ends with an output. This page starts with
a responsibility and ends with a system that can do more than it could before.

- People **stop driving sessions**: agents work inside a defined process and reach out when they need someone.
- Access is **limited by role**: a shipped reviewer is read-only, with its write and shell tools forbidden.
- The unit of the story is a **responsibility**, not a run.
- Agents are **the main participants**, and a person joins at decisions.
- The result of work is **a better environment**, not only a finished task.

The sixth step is the one an orchestrator does not have: a resident picks up a
tool from a peer and retires a duplicate, and the next problem starts further
along.

## What ships and what does not

| Idea | State | Evidence |
|---|---|---|
| Workspace with charter, trust, budget and log | **On by default** | `plugin-realms`, `rest_realms.py`. A fresh local install creates a Blank workspace and starts its resident. Templates with standing jobs need the event bus, so they stop at the jobs step locally. |
| Session telemetry | **On by default** | The Telemetry tab is now a default session tab. Spans are stored by Forge. |
| OpenTelemetry export | **Off until configured** | `observability.enabled` needs a collector's endpoints, so it cannot default on. The page says "connect a collector". |
| Security | Ships, with limits | OIDC, scoped workload tokens and OpenShell sandboxes. A local install has no login, and the page says so. |
| Multi-tenancy | Ships, with limits | Sessions, Realms, Memory and credentials carry a tenant. Isolation is not one single mechanism across all stores. |
| Residents react to inputs | Ships | `src/ravn/adapters/triggers/`: schedules, bus events, polled conditions, Memory changes and the ticket queue. Messages arrive through rooms and channels. |
| Workspace trust levels | **Mostly stored, not enforced** | Agents read only the `build` grant (`src/ravn/adapters/realm/client.py`). The UI shows "asks before deploy", but nothing enforces it. The page only claims how far a resident may change itself. |
| Workspace setup from a sentence | Ships, simple | A regex in the browser (`plugin-realms/src/domain/sentence.ts`), not the resident. The page says "Setup". |
| Workspaces outside code | **Limited** | Every template requires a repository. The page's example is a resident, not a Workspace. |
| Residents with mandates, Activity and Inbox | Ships | `plugin-valkyrie`, resident runtime |
| Teams through workflows, with review and gates | Ships | 18 bundled workflows, `src/ting/workflows/` |
| Per-role tools, model and budget | Ships | Workflow stages set persona, model and budget (`src/ting/workflows/*.yaml`). Personas set tools (`src/ravn/personas/developer-security-plan-reviewer.yaml`). |
| Rooms shared by people and agents | Ships | `niuu.collaboration`, browser and Telegram |
| Direct peer communication, A2A | Ships | Flock mesh, `src/ravn` |
| Tool built, verified and adopted | Ships | `build_tool.py`, `tool-skill-builder.yaml` |
| Peer adoption | Ships, partial | Tested tools only, and they re-verify. `docs/ravn/flock-capability-gate.md` shows proposals do not reach every resident. The copy says "can adopt a tested tool". |
| Duplicate tools retired | Ships | Evolution report. Unused and unrunnable tools are also retired, so the page says a tool stays "while it stays useful". |
| Resident decision history | Ships, needs a database | In memory without a DSN (`src/ravn/adapters/valkyrie_history/__init__.py`), and no repo chart sets one. The page says Activity "shows" decisions, not that they persist. |
| Memory with beliefs and evidence | Ships | `plugin-mimir` |
| One goal and actor model across everything | **Direction** | `concepts/operating-system.md`. Only in the Direction section. |
| A resident's intrinsic drive | **Not claimed** | The June design names it as missing. The page says residents observe and judge, not that they set their own goals. |

## About the screens

- **Real:** the two workflow screens are from a fresh install of current `dev`.
  The Memory page is a real page, written from these docs.
- **Real component, sample conversation:** the chat shot is the product's
  `SessionChat` component with a written sample, because no live agent could run
  here. Replace it with a real session.
- **Sample data:** the Workspaces and Resident console screens are the real
  interface in the demo build, because a fresh install has no residents. The
  Telemetry screen is the real tab on a real session, with sample spans posted
  through Forge's span API, because no live agent could run here. The page says so. Internal hostnames in that sample data were swapped for neutral ones at
  capture time, and the clock was set so timestamps read naturally.
- **Better:** recapture these from a real resident. Nothing on this machine could
  do it: the built-in browser is signed out of the live platform.
