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

Use the same `consumer_group` only for interchangeable workers where exactly
one replica should process each event, for example multiple k8s event normalizer
replicas:

```yaml
mesh:
  nats:
    consumer_group: k8s-signal-normalizers
```

Do not share a consumer group across different roles. A k8s watcher, resident
Valkyrie, ODIN observer, and UI replay surface should normally have distinct
subscriptions so they all receive the operational trail.

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
