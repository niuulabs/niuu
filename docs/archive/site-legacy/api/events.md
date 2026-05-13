# Events API

The event pipeline ingests session events and fans them out to configured sinks.

All endpoints are prefixed with `/api/v1/forge/events`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/events` | Ingest a single event |
| `POST` | `/events/batch` | Ingest a batch of events |
| `GET` | `/events/health` | Health status of all sinks |

Per-session event queries are under the [Sessions API](sessions.md):

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/sessions/{id}/events` | Query events with type/time filters |
| `GET` | `/sessions/{id}/events/counts` | Event type counts |
| `GET` | `/sessions/{id}/events/tokens` | Token burn timeline |

## Event types

| Type | Payload fields |
|------|---------------|
| `message_user` | `content_length`, `content_preview` |
| `message_assistant` | `content_length`, `content_preview`, `finish_reason` |
| `file_created` | `path`, `size_bytes` |
| `file_modified` | `path`, `insertions`, `deletions` |
| `file_deleted` | `path` |
| `git_commit` | `hash`, `message`, `files_changed` |
| `git_push` | `branch`, `commits_count`, `remote` |
| `git_branch` | `name`, `from_branch` |
| `git_checkout` | `branch` |
| `terminal_command` | `command`, `exit_code`, `duration_ms` |
| `tool_use` | `tool`, `arguments_preview`, `duration_ms` |
| `error` | `source`, `message` |
| `token_usage` | `provider`, `model`, `tokens_in`, `tokens_out` |
| `session_start` | `model`, `repo`, `branch` |
| `session_stop` | `reason`, `total_tokens` |

## Sinks

Events are dispatched to all configured sinks. Failures in one sink do not block others.

| Sink | Config key | Extra required |
|------|-----------|----------------|
| PostgreSQL | always enabled | — |
| RabbitMQ | `event_pipeline.rabbitmq.enabled` | `rabbitmq` |
| OpenTelemetry | `event_pipeline.otel.enabled` | `otel` |

See [Event Sinks](../backends/events.md) for backend configuration.
