# Ravn / Niuu Ownership Boundary

This boundary is architectural, not merely a packaging convention.

## Ravn is the autonomous agent

Ravn wraps models and agentic execution runtimes. Direct model providers such
as Nemotron and OpenAI-compatible APIs, and richer runtimes such as Codex,
Claude Code, and OpenCode, sit beneath Ravn behind runtime/model ports and
adapters.

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

## Niuu is the collaboration and infrastructure platform

Niuu owns:

- Valkyrie identity and lifecycle;
- Flokk membership, admission, roles, leases, and control-plane state;
- Flokk mesh provisioning and transport infrastructure;
- Skuld rooms, membership, presence, routing, and human-facing channels;
- workload identity, credentials, deployment, Kubernetes reconciliation,
  gateways, registries, and other platform infrastructure.

Niuu may provide capabilities and infrastructure to Ravn through ports and
dynamically configured adapters. It must not decide what an observation means
or what the resident should learn.

## Keep the communication mechanisms distinct

These are complementary paths, not synonyms:

1. **Flokk mesh:** direct agent-to-agent communication among Valkyries in a
   Flokk. This is not the A2A protocol.
2. **Skuld room:** a shared conversational room that multiple Ravns and humans
   can join. Telegram and similar integrations are room channels, not Ravn
   cognition.
3. **A2A:** the protocol Ravn uses to discover and interact with external
   agents, workflows, or larger agent systems outside direct Flokk-mesh
   communication.

Do not implement Flokk mesh communication by relabelling it as A2A, and do not
treat every Skuld room interaction as A2A.

## Operator input ownership

Ravn decides that input is required, persists the exact suspended case, and
decides how the answer changes its judgment. Skuld transports the request and
routes replies from a human or another room participant. The reply must return
to the exact Ravn case/run; Skuld does not own continuation semantics.

## Observability

Preserve W3C trace context across model/runtime calls, Ravn judgment and tool
activity, Flokk mesh messages, A2A tasks, Skuld room delivery, and operator
resume. Telemetry must keep ownership visible so model behavior, runtime
behavior, Ravn judgment, and Niuu infrastructure can be diagnosed separately.

## Dependency direction

- Runtime/model adapters implement Ravn-owned ports.
- Ravn domain and services depend on ports, never concrete Niuu infrastructure
  adapters.
- Niuu composes Valkyries, Flokks, meshes, rooms, identity, and deployment at
  platform composition roots.
- Existing code does not yet perfectly reflect this boundary. Changes should
  move toward it without introducing a second parallel agent loop or a
  speculative abstraction stack.
