# Ravn / Niuu Ownership Boundary

This boundary is architectural, not merely a packaging convention.

## Ravn is the agent runtime

Ravn wraps models and agentic execution runtimes. A Ravn does not have to be
autonomous. Running a Ravn as a long-lived, mostly self-sufficient steward of
an Environment makes it a **resident** (historically called a Valkyrie).

Ravn owns:

- judgment over observations and consequences;
- the autonomous action/tool loop;
- durable cases, continuation, and operator-wait state;
- learning and evidence-backed revision;
- capability discovery, tool construction, verification, adoption, and reuse;
- A2A discovery and task interaction with external agents, workflows, and
  agent systems; and
- the decision to inspect, research, ask, use a peer, use A2A, build, wait, act,
  or stop.

Do not move semantic judgment, learning policy, or capability-evolution
decisions into Niuu infrastructure. Do not add signal-name or environment-type
routers that replace Ravn's model judgment.

## Skuld is the runtime session gateway

Skuld manages sessions for runtimes such as Codex, Claude Code, OpenCode, and
Ravn. It owns runtime process lifecycle, authentication, delivery, transcript
capture, and channel/WebSocket adaptation. It does not own Ravn judgment,
learning, continuation, or collaboration semantics.

An ordinary ephemeral session must not pay for resident collaboration wiring
unless that capability is enabled in configuration.

## Niuu provides shared collaboration and infrastructure

Niuu owns:

- Valkyrie identity and lifecycle;
- Flokk membership, admission, roles, leases, and control-plane state;
- Flokk mesh provisioning and transport infrastructure;
- transport-neutral collaboration contracts, membership, presence, routing,
  replay, and huddle mechanics used by both Ravn and Skuld;
- workload identity, credentials, deployment, Kubernetes reconciliation,
  gateways, registries, and other platform infrastructure.

Niuu may provide capabilities and infrastructure to Ravn through ports and
dynamically configured adapters. It must not decide what an observation means
or what the resident should learn. Human-facing protocol translation remains
a Skuld adapter over the shared collaboration library; it is not the room
domain itself.

## Keep the communication mechanisms distinct

These are complementary paths, not synonyms:

1. **Flokk mesh:** direct agent-to-agent communication among Valkyries in a
   Flokk. This is not the A2A protocol.
2. **Collaboration room:** shared conversational state that multiple Ravns and
   humans can join. Its mechanics live in `niuu.collaboration`; Skuld exposes
   it through browser, Telegram, and other channels. Those channels are not
   Ravn cognition.
3. **A2A:** the protocol Ravn uses to discover and interact with external
   agents, workflows, or larger agent systems outside direct Flokk-mesh
   communication.

Do not implement Flokk mesh communication by relabelling it as A2A, and do not
treat every collaboration-room interaction as A2A.

## Operator input ownership

Ravn decides that input is required, persists the exact suspended case, and
decides how the answer changes its judgment. Shared collaboration mechanics
hold opaque reply context; Skuld transports the request and routes replies
from a human or another room participant. The reply must return to the exact
Ravn case/run; neither Skuld nor the shared library owns continuation semantics.

## Observability

Preserve W3C trace context across model/runtime calls, Ravn judgment and tool
activity, Flokk mesh messages, A2A tasks, Skuld room delivery, and operator
resume. Telemetry must keep ownership visible so model behavior, runtime
behavior, Ravn judgment, and Niuu infrastructure can be diagnosed separately.

## Dependency direction

- Runtime/model adapters implement Ravn-owned ports.
- Ravn domain and services depend on ports, never concrete Niuu infrastructure
  adapters.
- `niuu.collaboration` imports neither Ravn nor Skuld. It contains mechanics,
  not semantic projections or delivery protocols.
- Ravn adapters project Ravn events into neutral collaboration events.
- Skuld adapters expose neutral collaboration events through configured
  channels and may depend on the shared library, never the reverse.
- Niuu composes residents, Flokks, meshes, collaboration, identity, and
  deployment at platform composition roots.
- Existing code does not yet perfectly reflect this boundary. Changes should
  move toward it without introducing a second parallel agent loop or a
  speculative abstraction stack.
