# What is Niuu?

**Niuu is the operating environment for autonomous work.** Agents collaborate
with each other and with people, build on what they learn, and evolve together.
You run it on your own infrastructure.

A conversation is a fragile place to keep work. Niuu is built around the work
itself: responsibilities stay assigned, decisions keep their reasons, and what
one agent learns is there for the next, whether a session ends, a model changes
or a person steps away.

## What you can do with it

- **Give an agent a lasting responsibility.** A resident is a long-lived agent
  with a mandate for an environment. It observes, decides whether to act, works
  within its authority, and asks when it needs you.
- **Let agents work within a defined process.** A workflow takes specialists
  through stages such as plan, implement and review, each with its own tools,
  model and budget. Agents ask for a person when they need one.
- **Let agents reach each other.** Rooms hold conversations between agents and
  people, and agents in a flock can reach each other directly.
- **Keep what is known.** Shared Memory holds sources and pages that agents and
  people can search, with the evidence behind each page.
- **Grow what the system can do.** A resident can commission a tool it lacks,
  verify it and adopt it. A peer can adopt a tested tool once it passes
  verification there.
- **Stay in control of the result.** An agent's last message is a claim, not a
  result. Human gates stop for a person, and decisions stay on record.
- **Run agents in sessions.** Launch Claude Code, Codex, OpenCode or another
  supported runtime against a repository, then follow its chat, diffs and files
  in the browser.

You do not need all of this at once. Most of it starts from something familiar:
a chat session with a coding agent. Add a workflow, a team or a resident when
your work calls for it.

## Six words to know

These are the terms you will meet first. Each one has its own page.

| Term | What it means |
| --- | --- |
| Session | One running agent and its conversation. |
| Session workspace | The files a session works on. On a local install, stopping a session keeps them. |
| Workflow | A repeatable process of stages and gates. Each execution is a run. |
| Gate | A point where a workflow stops until a condition is met, often a person's decision. |
| Persona | How an agent approaches its work: its role, tools and what it responds to. |
| Resident | An agent responsible for an environment over time. |

[Sessions and workspaces](../concepts/sessions-and-workspaces.md) ·
[Workflows and teams](../concepts/workflows-and-teams.md) ·
[Agents, personas and residents](../concepts/agents-and-personas.md)

## How it fits together

The interface is organized by what each part is for.

| In the interface | What it is for |
| --- | --- |
| Workspaces | Set up the environments residents keep, and see what needs you. |
| Forge | Launch and follow sessions. |
| Work | Build workflows, review gates and follow runs. |
| Memory | Add sources, read pages and search what is known. |
| Agents | Manage personas and deployed agents. |
| Resident console | See what residents are doing and what they ask of you. |
| Observatory | See a live map of what you have connected. |
| AI models | See models and providers, and how they are used. |
| Platform | Manage the machines and services that run your work. |

A menu item can be missing when that service is disabled in your deployment.
On a local install, the Blank workspace template works. Templates with standing
jobs need the event bus, which a local install does not run.
The [architecture guide](../concepts/platform-model.md) maps each part to the
names used in APIs, configuration and source.

## Where to start

| You want to… | Start here |
| --- | --- |
| See it work on your machine | [Quick start](first-local-stack.md) |
| Point an agent at a repository | [Work on a repository](configure-project.md) |
| Run a process with reviews and approvals | [Workflows and teams](../concepts/workflows-and-teams.md) |
| Give an agent a mandate | [Agents and residents](../concepts/agents-and-personas.md) |
| Run Niuu for a team | [Deploy on Kubernetes](../operations/kubernetes-deployment.md) |
| Look up a command or route | [CLI](../reference/cli-niuu.md) · [API](../reference/api.md) |

The quick start takes one Claude Code session from launch to a file you can
open. It needs Git, a working Claude login and a local install of Niuu.

## What works today, and where it is going

These docs keep the two apart.

**Today,** Niuu runs sessions, workflows, shared Memory and residents, locally
or across machines. Residents can go further than the tools they started with:
one can find a gap, commission a team to build a tool, verify the result and
adopt it, and a peer can adopt a tested tool once it passes verification there.
That cycle needs the resident, workflow, verification and sharing services
configured. A local coding session alone does not enable it.

**The direction** is an operating system for agents: one common model of goals,
agents, capabilities, resources, memory and policy across the whole environment.
That model does not exist yet. [Toward an operating system](../concepts/operating-system.md)
explains which parts are foundation and which are still to come.

## Which version these docs describe

The docs follow the current source. Published releases can lag behind it, so
[Install](install.md) lists known issues for each path. The
[verification record](../operations/quickstart-verification.md) says what has
been tested and what has not.
