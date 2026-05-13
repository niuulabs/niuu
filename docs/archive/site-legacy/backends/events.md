# Event Sinks

Session events are dispatched through a pipeline to one or more sinks. Each sink implements the `EventSink` port. Failures in one sink do not block others.

## PostgreSQL (always enabled)

Events are persisted to the `session_events` table. This is the default sink and cannot be disabled.

```yaml
event_pipeline:
  postgres_buffer_size: 1  # Flush after N events (1 = immediate)
```

## RabbitMQ

Publishes events to a topic exchange.

```yaml
event_pipeline:
  rabbitmq:
    enabled: true
    url: "amqp://guest:guest@rabbitmq:5672/"
    exchange_name: "volundr.events"
    exchange_type: "topic"
```

Requires the `rabbitmq` extra:

```bash
uv sync --extra rabbitmq
```

Events are published with routing keys based on event type (e.g., `session.message_user`, `session.git_commit`).

## OpenTelemetry

Exports events as OTel spans and metrics following GenAI semantic conventions (v1.39+).

```yaml
event_pipeline:
  otel:
    enabled: true
    endpoint: "http://collector:4317"
    protocol: "grpc"
    service_name: "volundr"
    provider_name: "anthropic"
    insecure: true
```

Requires the `otel` extra:

```bash
uv sync --extra otel
```

Compatible with any OTLP collector: Grafana Alloy, Jaeger, Tempo, etc.

## Disabling sinks

- PostgreSQL: always on
- RabbitMQ: set `event_pipeline.rabbitmq.enabled: false` (default)
- OpenTelemetry: set `event_pipeline.otel.enabled: false` (default)

If the required Python package is not installed, the sink logs a warning and is skipped.
