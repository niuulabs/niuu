# Ravn, Skuld, and collaboration ownership

This is the compact architectural contract for agent runtimes, resident
autonomy, rooms, direct mesh communication, and A2A.

## The nouns

- **Ravn** is an agent runtime. It wraps a model or richer execution runtime and
  owns judgment, learning, capability evolution, delegation decisions, and A2A.
  A Ravn is not inherently autonomous.
- **Resident** is a long-lived autonomous Ravn acting as steward of an
  Environment. “Valkyrie” is the historical product term for the same role.
- **Skuld** is a runtime session gateway. It manages Codex, Claude Code,
  OpenCode, and Ravn sessions plus authentication, transcripts, delivery, and
  human-facing channels.
- **Collaboration** is a small shared library. It owns neutral room membership,
  presence, routing, huddle history, replay, and opaque reply context. Both
  Ravn and Skuld use it.
- **Flokk mesh** is direct agent-to-agent communication between members of a
  Flokk. It is not the A2A protocol.
- **A2A** is Ravn's protocol path to external agents and agent systems.

## One path through the system

```text
observation -> Ravn judgment -> action / research / ask / delegate / learn
                                  |
                                  v
                    Ravn collaboration projection
                                  |
                                  v
                   shared collaboration mechanics
                                  |
                                  v
                     Skuld channel/WebSocket adapter
                                  |
                                  v
                               operator
```

The return path preserves the exact Ravn-provided continuation context. Skuld
and the shared library transport it as opaque data; Ravn decides how the answer
changes the suspended case.

## What is deliberately not encoded

No collaboration or Skuld code maps signal names, machine types, or an
`environment_type` enum to prescribed behavior. Any observation may reach the
resident. Ravn and its model judge what it means, whether more research or a
tool is required, and whether to ask an operator or peer.

Likewise, relays carry evidence, not prompts that tell a model what conclusion
to reach. Ravn declares the event subscriptions relevant to its persona; the
shared relay supplies the matching facts.

## Runtime profiles

An ordinary ephemeral Skuld session loads its configured runtime transport and
human delivery surfaces only. Collaboration, mesh bridging, observation relay,
and resident behavior are opt-in composition. Resident deployments enable the
same shared collaboration capability and attach a Ravn with matching room and
Environment identity.

## Trace contract

W3C trace context crosses Ravn judgment, Ravn-to-collaboration projection,
Skuld receipt, operator attention, directed reply delivery, and Ravn resume.
Span attributes identify the owning component. This makes the resident HUD and
Tempo traces evidence of the actual path rather than inferred log narratives.
