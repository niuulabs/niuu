# Running

## API server

```bash
# Production (multi-worker)
uv run volundr

# Development (auto-reload)
uv run uvicorn volundr.main:app --reload --port 8080
```

The server binds to `0.0.0.0:8080` by default. Override with environment variables:

```bash
HOST=127.0.0.1 PORT=9000 WORKERS=2 uv run volundr
```

Interactive API docs are available at `/docs` (Swagger UI) and `/redoc`.

## Skuld broker

Skuld runs as a separate process inside session pods. For local development:

```bash
uv run skuld
```

Skuld configuration uses its own settings class (`SkuldSettings`) and is typically configured through the Helm chart.

## Web UI

```bash
cd web-next
pnpm install --frozen-lockfile
pnpm dev
```

Serves at `http://localhost:5173` with hot reload. The app entrypoint lives in `web-next/apps/niuu`, and runtime service wiring is configured from the `web-next` workspace.

## Docker

```bash
# Build the API image
docker build -t volundr:latest .

# Run with external PostgreSQL
docker run -p 8080:8080 \
  -e DATABASE__HOST=host.docker.internal \
  -e DATABASE__PASSWORD=secret \
  volundr:latest
```

## CLI local mode

If you installed the `niuu` CLI binary, the simplest way to run everything:

```bash
# Start all services (PostgreSQL, API server, reverse proxy)
niuu volundr up

# Check service status
niuu volundr status

# Stop all services
niuu volundr down
```

See [CLI Reference](../user-guide/cli.md) for the full command list.

## Health check

```
GET /health
```

Returns `{"status": "healthy"}` when the server is ready.
