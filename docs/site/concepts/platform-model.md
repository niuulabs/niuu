# Architecture and composability

Niuu is a set of microservices and shared libraries for running and coordinating
agents. The platform host exposes the configured services through a common API
surface and web application. A local host can run them in one process; distributed
deployments can run service instances separately.

![Niuu architecture](../images/niuu-architecture-light.png){ .architecture-light }
![Niuu architecture](../images/niuu-architecture-dark.png){ .architecture-dark }

## Control belongs to agents and humans

The browser, APIs, and channels are ways to interact with the same platform.
An agent can launch work, inspect state, participate in a room, or ask another
participant for input. A human can do the same through the available interfaces.
The interface used does not determine who makes a decision.

## Services own distinct parts of the work

| Component | Owns | Reach for it when… |
| --- | --- | --- |
| Völundr / Forge | Workspace and session lifecycle, launch configuration, review surfaces | You need a managed place for an agent to work |
| Skuld | Runtime process lifecycle, authentication, delivery, transcripts, and channel adaptation | A session needs to communicate with services or participants |
| Ravn | Agent judgment, model/runtime use, learning, continuation, and A2A interaction | You need Ravn behavior, directly or as a resident |
| Ting | Workflow definitions, dispatch, stages, gates, and runs | Work needs a repeatable process |
| Mímir | Durable sources, knowledge pages, retrieval, and curation | Information should survive conversations |
| Bifröst | Model catalog, provider configuration, routing, and usage | Model access should be shared across clients |
| Guild | Groups and discovers instances of the same service | Work needs to find the right service instance |
| Observatory | Topology and observability | You need to understand relationships and failures |
| Sleipnir | Event transport abstractions | Services need local or distributed event delivery |

These names describe responsibilities, not a checklist of things to install.
Start with the [local quick start](../get-started/first-local-stack.md), then
add the services required by your workload.

## Agent runtimes and the session gateway

Ravn, Claude Code, Codex, and OpenCode can execute agent work. Skuld connects
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
| A2A | Ravn's protocol for discovering and interacting with independent agents and agent systems |

Rooms and mesh mechanics live in shared Niuu libraries. Skuld adapts channels to
those contracts. Ravn decides what a message means and how to act on it. When a
resident asks for input, the response must return to the suspended Ravn case;
transporting the response is separate from deciding how to resume.

## Where it runs

Local processes use the host account and filesystem. OpenShell provides a
sandbox backend. Kubernetes provides cluster workloads and persistent service
deployments; OpenShell may itself use Kubernetes as its compute driver.
These choices are related, so they should not be treated as three interchangeable
security boundaries. See [sessions and workspaces](sessions-and-workspaces.md)
and [OpenShell](../operations/openshell-runtime.md).
