# Event Pipeline

Session events (messages, file changes, git operations, token usage) flow through the event pipeline to configured sinks. Multiple sinks can run simultaneously.

## Sinks

| Sink | Description | Requires |
|------|-------------|----------|
| PostgreSQL | Always active, persists events to DB | Nothing (built-in) |
| RabbitMQ | Publishes to AMQP exchange | `rabbitmq` extra |
| OpenTelemetry | Exports to OTLP collector | `otel` extra |

## RabbitMQ

```yaml
event_pipeline:
  rabbitmq:
    enabled: true
    url: "amqp://guest:guest@rabbitmq:5672/"
    exchange_name: "volundr.events"
    exchange_type: "topic"
```

Install the extra dependency:

```bash
uv sync --extra rabbitmq
```

## OpenTelemetry

```yaml
event_pipeline:
  otel:
    enabled: true
    endpoint: "http://otel-collector:4317"
    protocol: "grpc"
    service_name: "volundr"
    insecure: true
```

Install the extra dependency:

```bash
uv sync --extra otel
```

Follows OTel GenAI semantic conventions (v1.39+).

## SSE Streaming

Real-time session state updates are available via Server-Sent Events:

```
GET /api/v1/forge/sessions/stream
```

These use an in-memory broadcaster. Events are not persisted through this channel.

## Event Health

Check sink status:

```
GET /api/v1/forge/events/health
```

Returns the connection state of each configured sink.
