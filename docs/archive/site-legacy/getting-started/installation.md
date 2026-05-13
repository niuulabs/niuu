# Installation

Three ways to install Volundr, from simplest to most production-ready.

---

## Prerequisites

Not all prerequisites apply to every method. Check the table for your path.

| Prerequisite | CLI Binary | From Source | Kubernetes |
|-------------|:---:|:---:|:---:|
| Python 3.11+ (3.12 recommended) | -- | Yes | -- |
| PostgreSQL 12+ | Bundled (embedded mode) | Yes | Helm chart handles it |
| Node.js 22+ | -- | Only for web UI dev | -- |
| Kubernetes 1.24+ | -- | -- | Yes |
| Helm 3.8+ | -- | -- | Yes |
| [uv](https://docs.astral.sh/uv/) package manager | -- | Yes | -- |

---

## Method 1: CLI Binary

The fastest path. Download a pre-built binary, answer a few questions, and you're running.

```bash
# Download from GitHub releases (pick your platform)
# https://github.com/niuulabs/volundr/releases

# macOS (Apple Silicon)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-darwin-arm64 -o niuu

# macOS (Intel)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-darwin-amd64 -o niuu

# Linux (x86_64)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-linux-amd64 -o niuu

# Linux (ARM64)
curl -fsSL https://github.com/niuulabs/volundr/releases/latest/download/niuu-linux-arm64 -o niuu

chmod +x niuu
sudo mv niuu /usr/local/bin/

# Initialize (interactive wizard)
niuu volundr init

# Start everything
niuu volundr up
```

`niuu volundr init` walks you through runtime selection, API keys, database mode, and GitHub configuration. `niuu volundr up` starts PostgreSQL (if you chose embedded mode), the API server, and a reverse proxy.

Open [http://localhost:8080](http://localhost:8080).

See the [Quick Start](quick-start.md) for a step-by-step walkthrough, or the [Local Quickstart](local-quickstart.md) for a full end-to-end guide.

---

## Method 2: From Source

For developers contributing to Volundr.

### Clone and install

```bash
git clone https://github.com/niuulabs/volundr.git
cd volundr

# Install dependencies
uv sync --dev
```

### Optional extras

Install only what you need:

```bash
uv sync --extra rabbitmq   # RabbitMQ event sink (aio-pika)
uv sync --extra k8s        # Kubernetes client (kubernetes-asyncio)
uv sync --extra otel       # OpenTelemetry export (traces + metrics)
```

Or install everything:

```bash
uv sync --all-extras --dev
```

### Set up the database

```bash
# Local PostgreSQL
createdb volundr

# Or via Docker
docker run -d --name volundr-pg \
  -e POSTGRES_USER=volundr \
  -e POSTGRES_PASSWORD=volundr \
  -e POSTGRES_DB=volundr \
  -p 5432:5432 \
  postgres:16
```

Tables are auto-created on startup in development mode.

### Configure and run

```bash
cp config.yaml.example config.yaml
# Edit config.yaml with your settings

uv run volundr
```

### Web UI (optional)

```bash
cd web-next
pnpm install --frozen-lockfile
pnpm dev
```

---

## Method 3: Kubernetes (Helm)

For production deployments. This is how Volundr is meant to run.

```bash
helm install volundr oci://ghcr.io/niuulabs/charts/volundr \
  --namespace volundr --create-namespace \
  --values your-values.yaml
```

Minimum `values.yaml`:

```yaml
anthropic:
  apiKey: sk-ant-...

github:
  token: ghp_...

postgresql:
  auth:
    password: a-real-password
```

See the [Helm Deployment Guide](../deployment/helm.md) for the full reference: ingress configuration, resource limits, persistent storage, TLS, and production hardening.

---

## Verifying the installation

Regardless of method, you can verify things are working:

```bash
# Health check
curl http://localhost:8080/health

# API version
curl http://localhost:8080/api/v1/version
```

---

## Next steps

- [Quick Start](quick-start.md) -- get running in 5 minutes
- [Local Quickstart](local-quickstart.md) -- full end-to-end walkthrough
- [First Session](first-session.md) -- create your first AI coding session
- [Configuration](configuration.md) -- full configuration reference
- [Troubleshooting](troubleshooting.md) -- common issues and solutions
- [Helm Deployment](../deployment/helm.md) -- production Kubernetes deployment
