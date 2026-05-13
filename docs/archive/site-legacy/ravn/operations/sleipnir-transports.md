# Sleipnir Transport Ladder

Sleipnir ships five transport adapters.  Pick the one that matches your
deployment topology — each rung of the ladder adds capability at the cost of
more infrastructure.

## The Ladder

```
InProcess → NNG → Webhook → Redis Streams → NATS / RabbitMQ
```

| Transport | Topology | Broker needed? | Multi-host? | Persistence |
|-----------|----------|---------------|-------------|-------------|
| **InProcess** | Single process | No | No | No |
| **NNG** | Multi-process, single node | No | No | No |
| **Webhook** | Multi-host, any network | No | **Yes** | No |
| **Redis Streams** | Multi-host | Redis | Yes | Yes |
| **NATS** | Multi-host, production | NATS | Yes | Yes (JetStream) |
| **RabbitMQ** | Multi-host, production | RabbitMQ | Yes | Yes (durable queues) |

---

## InProcess

```yaml
sleipnir:
  transport: in_process
```

Uses asyncio queues.  Zero network overhead.  Suitable for standalone Ravn,
single-process Pi mode, and unit tests.

**Class**: `sleipnir.adapters.in_process.InProcessBus`

---

## NNG (nanomsg next generation)

```yaml
sleipnir:
  transport: nng
  nng:
    address: "ipc:///tmp/sleipnir.sock"
```

Uses pynng PUB/SUB sockets.  Works across processes on the same node via IPC,
or across nodes via TCP.  No broker.  Requires `pip install pynng`.

**Class**: `sleipnir.adapters.nng_transport.NngTransport`

---

## Webhook (HTTP POST)

```yaml
sleipnir:
  transport: webhook
  webhook:
    publish_urls:
      - http://tyr:8080/sleipnir/events
      - http://volundr:8000/sleipnir/events
    listen_port: 8090
```

HTTP POST transport.  No broker required — each service POSTs events directly
to its peers' endpoints.  Works across hosts and networks as long as each node
is reachable over HTTP.

**When to use**: you need multi-node event delivery but cannot (or don't want
to) run a broker.  Good for small clusters, edge deployments, and services that
already expose an HTTP port.

**Retry policy**: up to 3 attempts with exponential back-off (1 s → 2 s → 4 s).
After all attempts are exhausted the event is logged as a warning and dropped
(fire-and-forget).

**Connection pooling**: a single `httpx.AsyncClient` is shared across all
publishes so HTTP connections are reused.

**Pattern matching**: application-level fnmatch on `event_type`, identical to
all other transports.

**Class**: `sleipnir.adapters.webhook.WebhookTransport`

Mounting the subscriber into an existing FastAPI app:

```python
from sleipnir.adapters.webhook import WebhookSubscriber

sub = WebhookSubscriber()
app.include_router(sub.router)          # POST /sleipnir/events
async with sub:
    await sub.subscribe(["ravn.*"], my_handler)
```

---

## Redis Streams

```yaml
sleipnir:
  transport: redis
  redis:
    url: redis://localhost:6379
    stream_prefix: sleipnir
```

One Redis stream per namespace (e.g. `sleipnir:ravn`, `sleipnir:tyr`).
Consumer groups for load distribution and optional replay from offset on
startup.  Requires `pip install redis[hiredis]`.

**Class**: `sleipnir.adapters.redis_streams.RedisStreamsTransport`

---

## NATS JetStream

```yaml
sleipnir:
  transport: nats
  nats:
    servers:
      - nats://localhost:4222
    stream: sleipnir
```

Production event transport.  JetStream persistence, consumer groups, and
at-least-once delivery.  Requires `pip install nats-py`.

**Class**: `sleipnir.adapters.nats_transport.NatsTransport`

---

## RabbitMQ

```yaml
sleipnir:
  transport: rabbitmq
  rabbitmq:
    url: amqp://guest:guest@rabbitmq:5672/
    exchange: sleipnir.events
```

Primary durable broker.  AMQP topic exchange with durable queues for named
services.  Already in the ODIN infrastructure stack.  Requires
`pip install aio-pika`.

**Class**: `sleipnir.adapters.rabbitmq.RabbitMQTransport`

---

## Choosing a Transport

```
Running unit tests?                        → InProcess
Single host, multiple processes?           → NNG
Multiple hosts, no broker?                 → Webhook
Multiple hosts, need persistence?          → Redis Streams
Production, need at-least-once + replay?   → NATS or RabbitMQ
Already running ODIN/RabbitMQ?             → RabbitMQ
```
