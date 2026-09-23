# Environment NATS Mesh Patterns

Resident Valkyries use the existing Ravn flock mesh over Sleipnir NATS
JetStream. Do not add a Valkyrie-specific bus: configure `mesh.adapter: nats`
and let `SleipnirMeshAdapter` keep the Ravn mesh contract stable across NATS,
RabbitMQ, nng, and tests.

## Subject Shape

Use one Environment stream and subject prefix per deployment boundary:

```yaml
mesh:
  adapter: nats
  nats:
    servers:
      - nats://nats:4222
    stream_name: ravn_environment
    subject_prefix: ravn.environment
```

`SleipnirMeshAdapter` publishes Ravn mesh topics as Sleipnir events under
`ravn.mesh.<topic>`, and `NatsTransport` maps those to:

```text
<subject_prefix>.ravn.mesh.<topic>
```

Recommended resident topics:

```text
signal.kubernetes.*
signal.host.*
signal.inbox.*
signal.printer.*
environment.state
valkyrie.judgment
valkyrie.action.request
valkyrie.action.result
odin.attention
odin.escalation
learning.local
learning.flock
```

## Fan-Out And Worker Groups

Use separate subscribers without `consumer_group` when every Valkyrie or
observer must see every event. This is the right default for state, judgment,
attention, audit, and learning topics.

Use the same `consumer_group` only for interchangeable workers where one
replica at a time should process each event, for example multiple k8s event
normalizer replicas:

```yaml
mesh:
  nats:
    consumer_group: k8s-signal-normalizers
```

Do not share a consumer group across different roles. A k8s watcher, resident
Valkyrie, ODIN observer, and UI replay surface should normally have distinct
subscriptions so they all receive the operational trail.

## Delivery Guarantee

JetStream subscriptions are **at-least-once**, so handlers must be idempotent.
Use `event_id` as the idempotency key.

- A message is acked only after its handler returns.
- A handler that raises gets the message nak'd.  JetStream redelivers it after
  the next `nak_backoff_s` delay.
- On the `max_deliver`-th failed delivery the message is published to
  `<subject_prefix>.system.dlq.message` and terminated.  The DLQ record holds
  the reason, delivery count, stream sequence and the raw message.
- While a message waits in the subscription queue or runs in its handler, the
  subscriber pings JetStream every `ack_progress_interval_s`.  Slow handling
  therefore does not cause redelivery.  A crashed process stops pinging, and
  JetStream redelivers its messages `ack_wait_s` later.  A graceful stop naks
  them, so they are redelivered at once.
- A full queue (`ring_buffer_depth`) withholds acks rather than dropping
  events.  `max_ack_pending` (default: `ring_buffer_depth`) caps what
  JetStream pushes to each consumer.

```yaml
mesh:
  nats:
    ring_buffer_depth: 1000
    max_deliver: 5
    ack_wait_s: 30.0
    ack_progress_interval_s: 10.0   # must be < ack_wait_s
    nak_backoff_s: [1.0, 5.0, 30.0, 60.0]
```

A durable `consumer_group` consumer that already exists keeps its server-side
`ack_wait`, `max_deliver` and `max_ack_pending`, because the client binds to it
as-is.  The subscriber enforces `max_deliver` and the DLQ itself either way.
Apply the rest with `nats consumer edit`.  `core_subscriptions` use core NATS,
which has no acks or replay, so they stay at-most-once.

## Replay After Restart

Set `replay_from_sequence` when recovering a resident Valkyrie from a known
JetStream offset:

```yaml
mesh:
  nats:
    replay_from_sequence: 128
```

Replay is intended for resident recovery, incident reconstruction, and UI join
flows. Runtime code should store the last processed stream sequence or a
checkpoint in the Environment state store before using this in production.

## Correlation

Preserve both IDs through the resident chain:

```text
signal -> environment.state -> valkyrie.judgment -> action/result -> odin.*
```

`correlation_id` groups the immediate operational thread. `root_correlation_id`
links later actions, feedback, and learning back to the original signal or
huddle. Handlers should copy both when deriving state, judgment, action, and
learning events.

## Failure Logs

`SleipnirMeshAdapter` logs publish, handler, RPC publish, timeout, and reply
failures with:

```text
peer
environment
topic or reply_topic
event_type
target_peer
correlation_id
root_correlation_id
timeout_s
```

For k8s, host, inbox, and printer/Pi deployments, set `environment_id` on the
adapter when constructing the mesh so those logs can be tied back to the
Environment that needs attention.
