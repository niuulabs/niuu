# Architecture and composability

Niuu is a distributed environment in which agents work, collaborate, and evolve.
Its microservices and shared libraries provide execution, coordination,
communication, memory, identity, and discovery. Agents can use those services to
pursue mandates, commission teams, and share capabilities across environments.

The platform host exposes configured services through a common API surface.
A local host can run them in one process; distributed deployments can run
service instances separately. These are building blocks for the
[operating system for agents](operating-system.md) we are working toward.

![Niuu architecture](../images/niuu-architecture-light.png){ .architecture-light }
![Niuu architecture](../images/niuu-architecture-dark.png){ .architecture-dark }

## Control belongs to agents and humans

The browser, APIs, and channels are ways to interact with the same platform.
An agent can launch work, inspect state, participate in a room, or ask another
participant for input. A human can do the same through the available interfaces.
The interface used does not determine who makes a decision.

## Composition through contracts

APIs, plugins, and dynamically configured adapters let you use services
independently, connect existing systems, and build your own clients. The web
application, a mobile client, an external workflow, and an agent can all operate
on the same services under their own identities and permissions.

Harness, runtime, model, and execution backend are separate choices. Support for
one does not imply support for every combination: each adapter must implement
the relevant contract, and model access and credentials must work from the
selected runtime. See [models and routing](model-routing.md) and
[sessions and workspaces](sessions-and-workspaces.md).

## Moving parts and implementation names

The documentation describes services by their responsibilities. This table maps
those responsibilities to the names used in the UI, configuration, and source.

| Moving part | Responsibility | Implementation name |
| --- | --- | --- |
| Agent runtime | Judgment, learning, capability evolution, continuation, and A2A | Ravn |
| Workflows and teams | Definitions, dispatch, stages, review gates, and runs | Ting |
| Workspaces and execution | Workspace and session lifecycle, launch configuration, and review surfaces | Völundr / Forge |
| Session gateway | Runtime processes, authentication, transcripts, and channel adaptation | Skuld |
| Shared memory and knowledge | Durable sources, knowledge pages, retrieval, and curation | Mímir |
| Model gateway | Model catalog, providers, routing, and usage | Bifröst |
| Service discovery | Instance registration, discovery, and routing | Guild |
| Event bus | Event transport between local and distributed services | Sleipnir |
| Observability | Topology, activity, and relationships across the system | Observatory |

Choose the services required by your workload. The
[local quick start](../get-started/first-local-stack.md) is one small entry point.

## Agent runtimes and the session gateway

Ravn, Claude Code, Codex, and OpenCode can execute agent work. The session gateway connects
supported runtimes to Niuu's session interfaces. It handles the communication
and lifecycle around that work; it does not replace the runtime's reasoning.

Ravn can also run directly. Its distinctive responsibilities include judgment,
learning, capability evolution, and maintaining the state needed to continue
work after waiting for input. Making Ravn a resident adds autonomous stewardship
of an environment. Ordinary Ravn conversations do not require residency.

## Shared collaboration

Three mechanisms solve different problems:

| Mechanism | Purpose |
| --- | --- |
| Collaboration rooms | Shared conversation, membership, presence, delivery, and replay for multiple participants |
| Mesh | Direct communication among members of a flock, supported by Niuu's membership and transport infrastructure |
| A2A | Discovery and task interaction with independent agents and agent systems, owned by the agent runtime |

Rooms and mesh mechanics live in shared Niuu libraries. The session gateway adapts
channels to those contracts. The agent runtime decides what a message means and
how to act on it. When a resident asks for input, the response must return to its suspended case;
transporting the response is separate from deciding how to resume.

The event bus carries events between services through configured transports.
Events can supply observations or report outcomes; the agent runtime judges what
an observation means and what to do next.

## Where it runs

Local processes use the host account and filesystem. OpenShell provides a
sandbox backend. Kubernetes provides cluster workloads and persistent service
deployments; OpenShell may itself use Kubernetes as its compute driver.
These choices are related, so they should not be treated as three interchangeable
security boundaries. See [sessions and workspaces](sessions-and-workspaces.md)
and [OpenShell](../operations/openshell-runtime.md).
