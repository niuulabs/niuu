# Environment Event Taxonomy

Resident Valkyries use standard `SleipnirEvent` messages. The taxonomy extends
Sleipnir namespaces and catalog factories; it does not introduce a Valkyrie-only
envelope.

## Namespaces

```text
environment.*        Environment lifecycle, health, state, and replay cursors
signal.*             Normalized external signals from k8s, host, inbox, printer/Pi
valkyrie.state.*     Wakefulness, dream, health, and resident state
valkyrie.judgment.*  Operational judgments and recommended actions
valkyrie.action.*    Scoped action requests/results/failures
odin.court.*         ODIN review, decision, and dissent
attention.*          Suppress/watch/review/urgent routing decisions
feedback.*           Human/system feedback for future behavior
learning.*           Private, Environment, Flock, domain, shared learning flow
participant.*        Humans, agents, tools, and surfaces entering/leaving
room.*               Replayable huddle lifecycle and transcript events
```

## Payload Conventions

Environment identity:

```json
{
  "environment_id": "cluster-prod-a",
  "environment_type": "k8s"
}
```

Signals:

```json
{
  "signal_source": "kubernetes.events",
  "signal_kind": "kubernetes",
  "severity": "warning",
  "confidence": 1.0,
  "data": {"kind": "Pod", "name": "api-7d9", "reason": "Restarted"}
}
```

Judgments and actions:

```json
{
  "attention_tier": "watch",
  "recommended_action": "inspect_pod",
  "authority_boundary": "autonomous",
  "confidence": 0.84,
  "evidence": [{"event_id": "evt-...", "reason": "pod restarted"}]
}
```

Correlation:

```text
correlation_id links the operational thread.
causation_id points to the event that directly caused this event.
tenant_id scopes multi-tenant deployments when needed.
```

Replay:

```text
environment.replay.checkpointed records stream/cursor progress.
replay_from_sequence in NATS config rehydrates a resident Valkyrie or UI join.
```

## Event Chains

k8s:

```text
signal.kubernetes.event
environment.state.changed
valkyrie.judgment.recorded
valkyrie.action.requested
valkyrie.action.completed
learning.promoted
```

Inbox:

```text
signal.inbox.message
valkyrie.judgment.recorded
attention.escalated
room.opened
feedback.recorded
```

Printer/Pi:

```text
participant.joined
valkyrie.state.changed
signal.printer.event
valkyrie.judgment.recorded
attention.escalated
```

The deterministic examples live in `sleipnir.domain.valkyrie_examples` so the
NIU-1030 end-to-end demo can reuse the same event names and payload shape.
