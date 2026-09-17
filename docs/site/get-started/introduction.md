# What is Niuu?

**Niuu is where AI agents work, collaborate, and evolve.** It is a distributed,
composable ecosystem built around independent agents with mandates, coordinated
teams, interconnected workflows, and shared memory.

An agent can work on a bounded task or become a resident responsible for an
environment over time. Residents observe changes, make judgments, call on peers,
and acquire capabilities. People can participate in the same system: initiate
work, contribute context, review results, and help resolve decisions.

## From work to new capability

Consider a resident responsible for a cluster. A storage warning arrives, but
none of its existing tools can answer the diagnostic question. It commissions
an agent team to build a tool, verifies the result, and adopts it. Other residents
can then reuse that capability in their own environments.

This sequence was demonstrated across three clusters: a tool commissioned by
one resident propagated to residents in two others. It illustrates how execution,
collaboration, and learning connect. Reproducing it requires the resident,
workflow, verification, and sharing services to be configured; starting a local
coding session alone does not enable that cycle.

## Compose the environment you need

Niuu's APIs, microservices, plugins, and adapters let you use its parts
independently or together. A browser, mobile app, CLI, or another agent can be a
client. The choice of interface does not determine who initiates work.

The agent runtime owns judgment, learning, and capability evolution. The session
gateway connects supported runtimes to channels and services. Shared platform
services provide collaboration, knowledge, identity, discovery, model access,
and execution infrastructure. See
[architecture and composability](../concepts/platform-model.md).

## Where we are heading

The goal is an **operating system for agents**: the whole distributed environment
becomes the computer. Goals, actors, capabilities, resources, memory, and policy
provide a common model for work across machines and services. The UI is a way
into that running system.

Today's services are the foundation. The
[OS direction](../concepts/operating-system.md) explains the relationship between
those working components and the common system model still to be built.

## Choose a starting point

| You want to… | Start here |
| --- | --- |
| Run a coding agent and inspect its output | [First local session](first-local-stack.md) |
| Understand mandates and resident behavior | [Agents and residents](../concepts/agents-and-personas.md) |
| Coordinate specialists | [Workflows and teams](../concepts/workflows-and-teams.md) |
| Build with Niuu's services | [Architecture](../concepts/platform-model.md) |
| Operate services on shared infrastructure | [Deployment](../operations/kubernetes-deployment.md) |

The local quick start uses one Claude Code session to give you a small,
verifiable result. From there, [choose the capabilities](path-from-small-to-autonomous.md)
your work needs. You can also begin with a direct agent or an individual service.

## Know which version you are using

The docs describe the current source tree. Published binaries can lag behind it;
[installation](install.md) lists known release issues, and the
[verification record](../operations/quickstart-verification.md) separates tests
that passed from paths that still need live validation.
