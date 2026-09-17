# Toward an operating system for agents

Niuu is being built toward a distributed operating system for agents. The whole
environment is the computer: machines, models, services, and devices contribute
resources; agents and people work through the capabilities available there.
Browsers, terminals, mobile clients, and channels provide ways to observe,
direct, and participate in the running system.

This is the architectural direction. Today's Niuu provides many of the services
that support it; a unified model across those services remains work ahead.

## Work starts with intent and observations

A person can give the system a goal. A resident can observe a change in its
environment and decide that work is needed under its mandate. Either path can
lead to a bounded task, a team, a workflow, or a request for human input.

The intended OS model makes the goal durable beyond the particular agent,
session, or machine used to pursue it. Execution produces artifacts and
observable outcomes. Those outcomes inform subsequent decisions, and verified
improvements can become capabilities available for later work.

The agent runtime owns judgment, learning, and capability-evolution decisions in that
cycle. Shared Niuu infrastructure provides the contracts and mechanisms for
identity, authority, communication, execution, and persistence. The OS direction
preserves that boundary.

## How the existing pieces map

The operating-system analogy explains why these services belong together.
It does not imply that they already implement one universal kernel API.

| Operating-system concern | Niuu foundation today | Direction |
| --- | --- | --- |
| Work and execution lifecycle | Workspace sessions, workflow runs, agent tasks, and residents | Goals and actors with a common lifecycle across execution mechanisms |
| Scheduling and resources | Workflow dispatch, execution backends, and model routing | Coordinate resource and execution choices under common constraints |
| Communication | Events, shared rooms, flock mesh, and runtime-owned A2A | Addressable participants across environments while preserving each mechanism's contract |
| Memory and durable output | Working context, shared knowledge, workspaces, session history, and artifacts | Connect context, evidence, and outcomes across the lifetime of a goal |
| Authority | Identity, credentials, permissions, and trust boundaries | Consistent scoped authority over resources and actions |
| Discovery and inspection | Service registries, agent discovery, and topology views | A coherent view of actors, resources, and running work |
| Learning and evolution | Agent learning, tool construction, verification, adoption, and reuse | Evaluation and lineage that make improvements traceable across the system |

Workflow dispatch, model routing, and agent judgment remain distinct
responsibilities. Their contracts are the basis for composition. See the
[implementation map](platform-model.md#moving-parts-and-implementation-names)
for the component names used in configuration and source.

## Learning changes what the system can do

Shared memory lets agents reuse knowledge. Capability evolution goes further:
an agent can identify a gap, commission work to close it, verify the result,
and adopt a new tool. Other agents can reuse the result through configured
sharing and access mechanisms.

The cluster-resident demonstration described in the
[introduction](../get-started/introduction.md#from-work-to-new-capability)
shows that cycle: one resident's diagnostic need led to a tool that reached
residents in other clusters.

The broader OS direction includes common evaluation and lineage: which change
was attempted, what evidence supports it, who adopted it, and how to revise or
withdraw it when outcomes disappoint. Existing agent-runtime mechanisms provide part of
that foundation; this is not a claim that system-wide evolutionary scheduling
or universal lineage already exists.

## Infrastructure supplies resources

Today Niuu runs on existing host and deployment infrastructure. Local processes,
sandbox backends, and Kubernetes supply execution mechanisms. Their boundaries,
credentials, storage, and resource limits remain real operational concerns.

The long-term model brings compute, services, and physical devices into one
resource vocabulary. A provider or adapter exposes what a resource can do and
how it may be used. Hardware location affects availability and performance;
it should not force every agent to know each machine's implementation details.

A common goal/actor model, general resource handles, and unified scheduling,
evaluation, and lineage are architectural work ahead. The current APIs and
configuration remain the contracts for operating today's platform.

## Explore the foundation

- [Architecture and composability](platform-model.md): current service ownership and contracts.
- [Agents, personas, and residents](agents-and-personas.md): mandates, judgment, and continuation.
- [Workflows and teams](workflows-and-teams.md): coordinated execution.
- [Memory and knowledge](memory-and-knowledge.md): context, evidence, and shared learning.
