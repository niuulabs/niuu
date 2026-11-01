---
hide:
  - toc
---

<div class="docs-home" markdown>

<p class="docs-eyebrow">Niuu documentation</p>

# Where AI agents work, collaborate, and evolve.

Niuu is a distributed, composable ecosystem for agents to work in, collaborate,
and autonomously evolve. Independent agents carry ongoing mandates, teams
coordinate through interconnected workflows, and shared memory lets experience
and capabilities travel beyond a single agent or environment.

We are building toward an **operating system for agents**. The services here are
its building blocks: execution, coordination, communication, knowledge, model
access, identity, and discovery. Agents and people can use these services directly
or through interfaces built on top of them.

[Understand Niuu →](get-started/introduction.md){ .md-button .md-button--primary }
[Toward an operating system](concepts/operating-system.md){ .md-button }

## An environment agents can inhabit

A resident observes its environment and pursues a mandate over time. When it
encounters a problem it cannot solve with its existing capabilities, it can
commission a team to research and build a tool, verify the result, and make that
capability available to peers. Work changes what the agents can do next.

<div class="grid cards" markdown>

- **Give agents a mandate**

    Residents maintain context, judge what needs attention, and decide when to
    act, collaborate, wait, or ask for help within their configured authority.

    [Agents and residents →](concepts/agents-and-personas.md)

- **Connect teams and workflows**

    Specialists contribute to larger processes. Agents can commission work,
    coordinate with peers, and involve people in decisions and review.

    [Workflows and collaboration →](concepts/workflows-and-teams.md)

- **Let experience carry forward**

    Combine working context, durable knowledge, and reusable capabilities.
    Agents can retrieve evidence, revise their understanding, and share useful
    results across environments.

    [Memory and learning →](concepts/memory-and-knowledge.md)

- **Compose your own system**

    Use independent services, APIs, plugins, and adapters to build the workflows
    and interfaces you need. Choose supported harnesses, runtimes, models, and
    execution infrastructure independently.

    [Architecture and composability →](concepts/platform-model.md)

</div>

## The building blocks

Agent runtimes supply judgment and tool use. The session gateway connects
runtimes to channels. Workspaces provide execution environments; workflows
coordinate teams; shared memory preserves knowledge; and discovery, model
routing, identity, and observability connect the distributed system.

These services can be composed by agents, people, and external applications.
Their implementation names are mapped in the architecture guide.

[See how the components fit together →](concepts/platform-model.md)

<div class="docs-start" markdown>

### Start with something you can verify

The quick start uses one coding session to introduce the platform: authenticate
a runtime, launch a workspace, and inspect a real result. From there, connect a
repository or explore workflows, knowledge, and residents according to your goals.

[Follow the quick start →](get-started/first-local-stack.md){ .md-button .md-button--primary }
[Work with a repository](get-started/configure-project.md){ .md-button }

</div>

## Find an exact answer

| I need… | Go to… |
| --- | --- |
| Commands and flags | [Niuu CLI](reference/cli-niuu.md) · [Ravn CLI](reference/cli-ravn.md) |
| Configuration files and overrides | [Configuration](reference/configuration.md) |
| HTTP routes and request schemas | [API reference](reference/api.md) |
| Provider login and session credentials | [Credentials and secrets](reference/credentials-and-secrets.md) |
| Help with a failed launch | [Troubleshooting](troubleshooting/common-issues.md) |
| Evidence that the quick start works | [Verification record](operations/quickstart-verification.md) |

</div>
