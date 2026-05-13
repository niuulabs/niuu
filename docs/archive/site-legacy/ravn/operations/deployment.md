# Deployment

Ravn supports three deployment modes depending on your infrastructure.

## Pi Mode (Local)

Run Ravn on a single machine (Raspberry Pi, laptop, desktop) with local
networking and mDNS discovery.

```yaml
# ravn.yaml — Pi mode
llm:
  model: claude-sonnet-4-6

gateway:
  enabled: true
  channels:
    telegram:
      token_env: TELEGRAM_BOT_TOKEN
      allowed_chat_ids: [123456789]
    http:
      host: "0.0.0.0"
      port: 7477

discovery:
  adapter: mdns
  mdns:
    realm_key_env: RAVN_REALM_KEY

mesh:
  enabled: true
  adapter: nng

permission:
  mode: full_access
```

Start with:

```bash
ravn daemon -c ravn.yaml
```

### Multi-Pi Setup

Run multiple Ravn instances on the local network. They discover each other
via mDNS and form a flock:

```bash
# Pi 1 (coordinator)
RAVN_REALM_KEY=secret123 ravn daemon -c ravn.yaml -p autonomous-agent

# Pi 2 (worker)
RAVN_REALM_KEY=secret123 ravn daemon -c ravn-worker.yaml -p coding-agent
```

All instances with the same `RAVN_REALM_KEY` discover each other and can
delegate tasks via cascade.

## Infrastructure Mode (Kubernetes)

Run Ravn in Kubernetes with Sleipnir event backbone, PostgreSQL persistence,
and Kubernetes-native discovery.

```yaml
# ravn.yaml — Infrastructure mode
llm:
  model: claude-sonnet-4-6
  provider:
    adapter: ravn.adapters.llm.bifrost.BifrostAdapter
    kwargs:
      base_url: "http://bifrost.ravn.svc:8080"

sleipnir:
  enabled: true
  amqp_url_env: SLEIPNIR_AMQP_URL

initiative:
  enabled: true
  max_concurrent_tasks: 5

cascade:
  enabled: true

discovery:
  adapter: k8s
  k8s:
    namespace: ravn
    label_selector: "app=ravn-agent"

memory:
  backend: postgres
  dsn_env: RAVN_POSTGRES_DSN

checkpoint:
  backend: postgres
```

### Deployment

Ravn does not have its own Helm chart. In Kubernetes, deploy it as a
standalone binary (Nuitka build) or container with a `ravn.yaml` ConfigMap.
The platform charts (`charts/volundr`, `charts/tyr`, `charts/bifrost`,
`charts/mimir`, etc.) handle their respective services.

## Docker

### Terminal Sandboxing

Run agent bash commands in Docker containers for isolation:

```yaml
tools:
  terminal:
    backend: docker
    docker:
      image: "python:3.11-slim"
      network: none
      mount_workspace: true
```

### Docker Compose

Example `docker-compose.yml` for a full Ravn stack:

```yaml
services:
  ravn:
    image: ghcr.io/niuulabs/ravn:latest
    environment:
      - ANTHROPIC_API_KEY
      - SLEIPNIR_AMQP_URL=amqp://rabbitmq:5672
      - RAVN_POSTGRES_DSN=postgresql://ravn:secret@postgres:5432/ravn
    volumes:
      - ./ravn.yaml:/etc/ravn/config.yaml
      - ravn-data:/home/ravn/.ravn
    depends_on:
      - rabbitmq
      - postgres

  rabbitmq:
    image: rabbitmq:3-management
    ports:
      - "15672:15672"

  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: ravn
      POSTGRES_USER: ravn
      POSTGRES_PASSWORD: secret
    volumes:
      - pg-data:/var/lib/postgresql/data

volumes:
  ravn-data:
  pg-data:
```

## Config Overlays

Ravn supports environment-specific config files. The project includes
example configs:

| File | Purpose |
|------|---------|
| `ravn.tui.example.yaml` | TUI keybinding and layout configuration |
| `bifrost.pi.example.yaml` | Bifrost proxy setup for Pi mode |

Use the `RAVN_CONFIG` environment variable or `--config` flag to select
the appropriate overlay.

## Nuitka Binary

Ravn can be compiled into a single standalone binary using Nuitka:

```bash
# Build single binary (no Python required on target)
make ravn-binary
```

This produces a self-contained executable suitable for deployment to
minimal environments (containers, embedded systems, etc.).
