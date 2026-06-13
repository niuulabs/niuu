# Events Reference

Niuu services communicate through events, streams, and transport adapters.

## Event uses

- Session lifecycle updates
- Chat and activity streams
- Workflow dispatch and run state
- Topology and health updates
- Knowledge writes and lint state
- Human escalation and review signals

## Transports

Sleipnir provides transport abstractions across local processes and distributed backbones such as NATS, NNG, and RabbitMQ.
